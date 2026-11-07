"""Icon rendering — the Cerberus silhouette, tinted per ticket state.

The artwork in ``data/icons/src/cerberus.png`` is a pure black alpha silhouette,
which makes it usable as a *mask*: cairo fills it with whatever colour we want.
That is exactly what the tray needs, because the state must be readable from the
icon alone at 22 px — green valid, amber expiring, red expired, grey no ticket.

Both the tray pixmap and the GTK pixbuf come from the same code path, so the
icon in the window and the icon in the bar can never drift apart.

If the artwork is missing the app still runs: it falls back to a drawn key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import cairo

# Nord palette, matching the Noctalia bar and the desktop theme.
STATE_COLORS = {
    "valid": (0.639, 0.745, 0.549),  # nord14 green
    "expiring": (0.922, 0.796, 0.545),  # nord13 yellow
    "expired": (0.749, 0.380, 0.416),  # nord11 red
    "none": (0.600, 0.682, 0.749),  # nord9 blue-grey
    # nord12 orange, deliberately distinct from "expired": an unreadable cache
    # is a different problem from a ticket that ran out, and the user acts on
    # them differently (fix the tooling vs. sign in).
    "error": (0.816, 0.529, 0.439),
}

# Alpha for "no ticket" — the shape stays visible but reads as inactive.
STATE_ALPHA = {"none": 0.55}

SOURCE_ART = Path(__file__).resolve().parent.parent / "data" / "icons" / "src" / "cerberus.png"

# How much of the square the emblem occupies. The artwork is wide (1.54:1), so
# leave a little room on the long axis or the heads touch the edge.
FIT = 0.94

# The full three-headed emblem is the logo, but at 22 px three heads collapse
# into a blob. The tray therefore shows the centre head only — measured by
# rendering candidates at true 22 px and comparing, which is the only way to
# judge a 22 px icon. The launcher icon keeps all three heads.
HEAD_CROP = (0.28, 0.72, 0.00, 0.85)  # as fractions of the artwork

_source_cache: dict[str, Optional[cairo.ImageSurface]] = {}


def source_art(path: Optional[str] = None) -> Optional[cairo.ImageSurface]:
    """The full artwork, loaded once. ``None`` if it is missing or broken."""
    if path is not None:
        try:
            return cairo.ImageSurface.create_from_png(path)
        except (cairo.Error, OSError):
            return None
    return _region("full")


def _region(region: str) -> Optional[cairo.ImageSurface]:
    """Cached artwork, either whole or cropped to the centre head."""
    if region in _source_cache:
        return _source_cache[region]

    base: Optional[cairo.ImageSurface]
    try:
        base = cairo.ImageSurface.create_from_png(str(SOURCE_ART))
    except (cairo.Error, OSError):
        _source_cache[region] = None
        return None

    if region == "full":
        _source_cache[region] = base
        return base

    width, height = base.get_width(), base.get_height()
    fx0, fx1, fy0, fy1 = HEAD_CROP
    x0, y0 = int(width * fx0), int(height * fy0)
    crop_w, crop_h = int(width * fx1) - x0, int(height * fy1) - y0
    cropped = cairo.ImageSurface(cairo.FORMAT_ARGB32, crop_w, crop_h)
    cr = cairo.Context(cropped)
    cr.set_source_surface(base, -x0, -y0)
    cr.paint()
    cropped.flush()
    _source_cache[region] = cropped
    return cropped


def _fit_scale(src_w: int, src_h: int, box: int) -> tuple[float, float, float, float]:
    """Scale + offsets to centre the artwork in a ``box`` x ``box`` square."""
    scale = min(box / src_w, box / src_h) * FIT
    draw_w, draw_h = src_w * scale, src_h * scale
    return scale, (box - draw_w) / 2.0, (box - draw_h) / 2.0, 0.0


def _mask_from_art(box: int, region: str = "head") -> Optional[cairo.ImageSurface]:
    """The artwork's alpha, scaled into a ``box`` x ``box`` surface."""
    src = _region(region)
    if src is None:
        return None
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, box, box)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    scale, off_x, off_y, _ = _fit_scale(src.get_width(), src.get_height(), box)
    cr.save()
    cr.translate(off_x, off_y)
    cr.scale(scale, scale)
    cr.set_source_surface(src, 0, 0)
    # paint() copies colour+alpha; only the alpha matters for masking later,
    # and the source is opaque where it is inked, so this is a clean mask.
    cr.paint()
    cr.restore()
    surface.flush()
    return surface


def _draw_fallback_key(cr: cairo.Context, size: int, color: tuple[float, float, float]) -> None:
    """Drawn key, used only when the Cerberus artwork is unavailable."""
    s = float(size)
    cy = 0.455
    cr.set_source_rgba(*color, 1.0)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(s * 0.115)
    cr.new_sub_path()
    cr.arc(s * 0.30, s * cy, s * 0.165, 0.0, 2 * 3.141592653589793)
    cr.stroke()
    cr.move_to(s * 0.455, s * cy)
    cr.line_to(s * 0.885, s * cy)
    cr.stroke()
    cr.set_line_width(s * 0.10)
    cr.move_to(s * 0.705, s * cy)
    cr.line_to(s * 0.705, s * 0.675)
    cr.stroke()
    cr.set_line_width(s * 0.088)
    cr.move_to(s * 0.835, s * cy)
    cr.line_to(s * 0.835, s * 0.615)
    cr.stroke()


def surface_for_state(state: str, size: int = 22) -> cairo.ImageSurface:
    """A square ARGB surface with the silhouette tinted for ``state``."""
    color = STATE_COLORS.get(state, STATE_COLORS["none"])
    alpha = STATE_ALPHA.get(state, 1.0)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    mask = _mask_from_art(size)
    if mask is None:
        _draw_fallback_key(cr, size, color)
    else:
        # mask_surface uses the mask's alpha channel: fill with the state colour
        # wherever the silhouette is inked, leave the rest transparent.
        cr.set_source_rgba(color[0], color[1], color[2], alpha)
        cr.mask_surface(mask, 0, 0)

    surface.flush()
    return surface


def _argb32_big_endian(surface: cairo.ImageSurface) -> bytes:
    """Cairo gives native-endian premultiplied ARGB32; SNI wants big-endian.

    On little-endian machines that means swapping the R and B bytes of every
    pixel. Getting this wrong produces a blue icon where a red one belongs, so
    it is done explicitly rather than hoped for.
    """
    surface.flush()
    data = bytes(surface.get_data())
    out = bytearray(len(data))
    # native little-endian cairo ARGB32 = [B, G, R, A]; SNI wants [A, R, G, B]
    for i in range(0, len(data), 4):
        b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
        out[i], out[i + 1], out[i + 2], out[i + 3] = a, r, g, b
    return bytes(out)


def sni_pixmap(state: str, size: int = 22) -> list[tuple[int, int, bytes]]:
    """One-element list matching SNI's ``a(iiay)`` IconPixmap signature."""
    surface = surface_for_state(state, size)
    return [(size, size, _argb32_big_endian(surface))]


def gdk_pixbuf_for_state(state: str, size: int = 22):
    """A GdkPixbuf for GTK widgets, rendered at the same shape as the tray."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    surface = surface_for_state(state, size)
    data = _argb32_big_endian(surface)
    # Re-order to RGBA for GdkPixbuf. Cairo premultiplies; GdkPixbuf wants
    # straight alpha, so undo it. The buffer is rebuilt tightly packed, hence
    # the explicit rowstride of size*4 rather than cairo's own stride.
    rgba = bytearray(len(data))
    for i in range(0, len(data), 4):
        a, r, g, b = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a and a < 255:  # undo premultiplication
            r = min(255, r * 255 // a)
            g = min(255, g * 255 // a)
            b = min(255, b * 255 // a)
        rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3] = r, g, b, a
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(rgba)),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        size,
        size,
        size * 4,
    )


# ── application icon ─────────────────────────────────────────────────────────
# The tray icon is a bare tinted silhouette, which is right on a dark bar. The
# launcher icon is different: it must stay legible on light and dark
# backgrounds, in docks and in window lists, so it gets a slab behind it.

ICON_BG_TOP = (0.231, 0.259, 0.322)  # nord0-ish
ICON_BG_BOTTOM = (0.180, 0.204, 0.251)
ICON_GLYPH = (0.533, 0.753, 0.816)  # nord8 frost


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Rounded-slab launcher icon with the silhouette in frost blue."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    radius = size * 0.22
    _rounded_rect(cr, 0, 0, size, size, radius)
    gradient = cairo.LinearGradient(0, 0, size * 0.4, size)
    gradient.add_color_stop_rgb(0, *ICON_BG_TOP)
    gradient.add_color_stop_rgb(1, *ICON_BG_BOTTOM)
    cr.set_source(gradient)
    cr.fill()

    # Glyph, inset so it does not crowd the rounded corners. The launcher shows
    # the full three-headed emblem: there is room for it at these sizes, and it
    # is the logo people recognise.
    glyph_box = int(size * 0.74)
    mask = _mask_from_art(glyph_box, "full")
    if mask is None:
        _draw_fallback_key(cr, glyph_box, ICON_GLYPH)
    else:
        offset = (size - glyph_box) / 2.0
        cr.save()
        cr.translate(offset, offset)
        cr.set_source_rgb(*ICON_GLYPH)
        cr.mask_surface(mask, 0, 0)
        cr.restore()

    surface.flush()
    return surface


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5707963267948966, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5707963267948966)
    cr.arc(x + r, y + h - r, r, 1.5707963267948966, 3.141592653589793)
    cr.arc(x + r, y + r, r, 3.141592653589793, 4.71238898038469)
    cr.close_path()


def write_png(path: str, size: int = 256, state: str = "valid", *, app_icon: bool = True) -> str:
    """Render an icon PNG. Used by tools/make-icons.py, not at runtime."""
    surface = app_icon_surface(size) if app_icon else surface_for_state(state, size)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    surface.write_to_png(path)
    return path
