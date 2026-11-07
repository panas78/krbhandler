#!/usr/bin/env python3
"""Turn the raw clipart into a usable alpha silhouette.

The downloaded artwork (``cerberus-original.png``) is a black-on-white image
with the "transparent" checkerboard *painted into the pixels* — it has no alpha
channel at all. Using it directly as a cairo mask therefore fills a solid
rectangle instead of the silhouette.

This script keys the background out and writes ``cerberus.png`` with a real
alpha channel, which is what :mod:`krbctl.icons` masks with.

The keying is not a naive threshold, because the artwork's own anti-aliased
edges are the same greys as parts of the checkerboard:

    0        ink
    38..205  genuine anti-aliasing between ink and background  (keep, partial alpha)
    230      checkerboard square                                (drop)
    244      anti-aliasing between checkerboard squares        (drop)
    255      background                                        (drop)

So: pixels at L >= 215 are background and become fully transparent; everything
darker keeps alpha = (255 - L) / 255, which is the correct ink coverage for a
black glyph composited over near-white.

Run once after replacing the artwork, then regenerate the icon set:

    python3 tools/prepare-art.py && python3 tools/make-icons.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "icons" / "src" / "cerberus-original.png"
DST = ROOT / "data" / "icons" / "src" / "cerberus.png"

# Anything at or above this luminance is background, not ink.
BACKGROUND_THRESHOLD = 215


def has_alpha(surface: cairo.ImageSurface) -> bool:
    """True if any pixel is even partly transparent.

    Cairo's ARGB32 is native-endian, so on x86 the byte order is B, G, R, A —
    the alpha channel is byte 3 of each pixel, not byte 0.
    """
    data = bytes(surface.get_data())
    return any(data[i] for i in range(3, len(data), 4) if data[i] != 255)


def key_out_background(surface: cairo.ImageSurface) -> cairo.ImageSurface:
    """Return a new surface where only the ink keeps alpha."""
    width, height = surface.get_width(), surface.get_height()
    stride = surface.get_stride()
    source = bytes(surface.get_data())

    out_stride = width * 4
    target = bytearray(out_stride * height)

    for y in range(height):
        row = y * stride
        out_row = y * out_stride
        for x in range(width):
            index = row + x * 4
            # grayscale source: bytes are B, G, R (all equal); luminance = B
            luminance = source[index]
            if luminance >= BACKGROUND_THRESHOLD:
                continue  # leave transparent (already zeroed)
            coverage = (255 - luminance) / 255.0
            # Cairo's ARGB32 is premultiplied. The ink is black, so the colour
            # bytes stay 0 and only the alpha carries the coverage.
            alpha = int(round(coverage * 255))
            out_index = out_row + x * 4
            target[out_index + 3] = alpha

    out = cairo.ImageSurface.create_for_data(target, cairo.FORMAT_ARGB32, width, height, out_stride)
    out.mark_dirty()
    return out


def crop_to_content(surface: cairo.ImageSurface, pad_ratio: float = 0.0) -> cairo.ImageSurface:
    """Trim empty margins so FIT in icons.py behaves predictably."""
    width, height = surface.get_width(), surface.get_height()
    stride = surface.get_stride()
    data = bytes(surface.get_data())

    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = y * stride
        for x in range(width):
            if data[row + x * 4 + 3] > 8:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return surface  # nothing visible, leave it alone

    pad = int(round(max(max_x - min_x, max_y - min_y) * pad_ratio))
    min_x, min_y = max(0, min_x - pad), max(0, min_y - pad)
    max_x, max_y = min(width - 1, max_x + pad), min(height - 1, max_y + pad)
    crop_w, crop_h = max_x - min_x + 1, max_y - min_y + 1

    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, crop_w, crop_h)
    cr = cairo.Context(out)
    cr.set_source_surface(surface, -min_x, -min_y)
    cr.paint()
    out.flush()
    return out


def main() -> int:
    if not SRC.exists():
        print(f"error: raw artwork missing at {SRC}", file=sys.stderr)
        return 1

    art = cairo.ImageSurface.create_from_png(str(SRC))
    print(f"source: {art.get_width()}x{art.get_height()}")

    if has_alpha(art):
        print("source already has alpha — keying skipped")
        keyed = art
    else:
        print("no alpha channel: keying out the painted checkerboard")
        keyed = key_out_background(art)

    cropped = crop_to_content(keyed)
    cropped.write_to_png(str(DST))
    print(f"wrote {DST.relative_to(ROOT)} ({cropped.get_width()}x{cropped.get_height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
