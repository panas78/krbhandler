#!/usr/bin/env python3
"""Generate the hicolor icon set from data/icons/src/cerberus.png.

    python3 tools/make-icons.py

Run this after replacing the source artwork. The output PNGs are committed, so
installing the app never needs cairo on the build machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from krbctl import icons  # noqa: E402

SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def main() -> int:
    if icons.source_art() is None:
        print(f"error: source artwork not found at {icons.SOURCE_ART}", file=sys.stderr)
        return 1

    written = []
    for size in SIZES:
        target = ROOT / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "krbctl.png"
        icons.write_png(str(target), size)
        written.append(target)

    # A symbolic-ish tray fallback for icon themes that look one up by name.
    tray = ROOT / "data" / "icons" / "hicolor" / "22x22" / "apps" / "krbctl-symbolic.png"
    icons.write_png(str(tray), 22, "none", app_icon=False)
    written.append(tray)

    # Preview sheet for eyeballing all states at bar size.
    preview = ROOT / "data" / "icons" / "preview-states.png"
    _preview(str(preview))

    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    print(f"wrote {preview.relative_to(ROOT)}")
    return 0


def _preview(path: str) -> None:
    """States side by side, at tray size and 4x, plus the app icon."""
    import cairo

    states = ("valid", "expiring", "expired", "none", "error")
    cell, pad = 96, 16
    width = pad + len(states) * (cell + pad) + cell + pad
    height = cell + 2 * pad + 40
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.18, 0.20, 0.25)  # dark bar colour, to judge contrast
    cr.paint()

    for index, state in enumerate(states):
        mask = icons.surface_for_state(state, cell)
        x = pad + index * (cell + pad)
        cr.set_source_surface(mask, x, pad)
        cr.paint()
        cr.set_source_rgb(0.85, 0.87, 0.91)
        cr.select_font_face("sans")
        cr.set_font_size(13)
        cr.move_to(x, pad + cell + 20)
        cr.show_text(state)

    app = icons.app_icon_surface(cell)
    cr.set_source_surface(app, pad + len(states) * (cell + pad), pad)
    cr.paint()
    cr.set_source_rgb(0.85, 0.87, 0.91)
    cr.move_to(pad + len(states) * (cell + pad), pad + cell + 20)
    cr.show_text("app icon")
    surface.write_to_png(path)


if __name__ == "__main__":
    raise SystemExit(main())
