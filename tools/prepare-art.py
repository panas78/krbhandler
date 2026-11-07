#!/usr/bin/env python3
"""Turn the raw cartoon into a transparent-background icon source.

    python3 tools/prepare-art.py

The artwork arrives as a flat PNG on a plain white background. A luminance key
cannot be used: the body is a mid grey and the teeth are white, so keying by
brightness would make the dog transparent and keep the teeth. Instead the
background is found by flood filling inwards from the border, which removes
only the white that is connected to the edge and leaves interior whites (teeth,
eye highlights) untouched.

The flood fill produces hard 1-bit alpha. That is deliberate: a one-pixel white
fringe on an 800 px source is a fortieth of a pixel at 32 px, so cairo's own
downscaling removes it. Feathering here would only soften the outlines.

Output is written to data/icons/src/cerberus.png and is the file the icon
generator reads.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "icons" / "src" / "cerberus-raw.png"
OUT = ROOT / "data" / "icons" / "src" / "cerberus.png"

# A pixel counts as background if it is at least this bright on every channel.
# The artwork's background is pure white; the threshold only needs to absorb
# the slight compression noise around it.
WHITE = 232

# Space left around the artwork after cropping, as a fraction of the long edge.
MARGIN = 0.012


def _is_white(data: bytes, stride: int, x: int, y: int) -> bool:
    offset = y * stride + x * 4
    b, g, r = data[offset], data[offset + 1], data[offset + 2]
    return r >= WHITE and g >= WHITE and b >= WHITE


def key_background(surface: cairo.ImageSurface) -> tuple[int, int, int, int]:
    """Make the border-connected white transparent; return the content bbox."""
    width, height = surface.get_width(), surface.get_height()
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()

    background = bytearray(width * height)  # 1 = background
    queue: deque[tuple[int, int]] = deque()

    def push(x: int, y: int) -> None:
        index = y * width + x
        if background[index]:
            return
        if not _is_white(data, stride, x, y):
            return
        background[index] = 1
        queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x > 0:
            push(x - 1, y)
        if x + 1 < width:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y + 1 < height:
            push(x, y + 1)

    # Rewrite alpha in place. Everything the flood fill did not reach is
    # artwork and becomes fully opaque.
    min_x, min_y, max_x, max_y = width, height, -1, -1
    cleared = 0
    for y in range(height):
        row = y * stride
        for x in range(width):
            offset = row + x * 4
            if background[y * width + x]:
                # Premultiplied ARGB: zeroing all four channels is transparent.
                data[offset] = data[offset + 1] = data[offset + 2] = data[offset + 3] = 0
                cleared += 1
            else:
                data[offset + 3] = 255
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    surface.mark_dirty()
    if max_x < 0:
        raise SystemExit("key_background: nothing left after keying — check WHITE")
    print(f"  keyed out {cleared} background pixels")
    return min_x, min_y, max_x + 1, max_y + 1


def main() -> int:
    if not RAW.exists():
        print(f"missing source artwork: {RAW}", file=sys.stderr)
        return 1

    surface = cairo.ImageSurface.create_from_png(str(RAW))
    width, height = surface.get_width(), surface.get_height()
    print(f"  source {width}x{height}")

    x0, y0, x1, y1 = key_background(surface)

    margin = int(max(x1 - x0, y1 - y0) * MARGIN)
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(width, x1 + margin)
    y1 = min(height, y1 + margin)

    cropped = cairo.ImageSurface(cairo.FORMAT_ARGB32, x1 - x0, y1 - y0)
    cr = cairo.Context(cropped)
    cr.set_source_surface(surface, -x0, -y0)
    cr.paint()
    cropped.flush()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    cropped.write_to_png(str(OUT))
    print(f"  cropped to {x1 - x0}x{y1 - y0} (aspect {(x1 - x0) / (y1 - y0):.3f})")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
