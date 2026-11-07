"""Icon rendering with cairo — no external assets, no icon theme required.

The tray needs a real pixmap over D-Bus (``IconPixmap``), and shipping PNGs for
every state and DPI is worse than drawing four shapes in code. Drawing also lets
the colour track the ticket state exactly.
"""

from __future__ import annotations

from typing import Optional

import cairo

from .config import ACCOUNTS_FILE  # noqa: F401  (keeps import graph obvious)

# Nord-ish palette, matching the Noctalia bar and the desktop theme.
STATE_COLORS = {
    "valid": (0.639, 0.745, 0.549),  # nord14 green
    "expiring": (0.922, 0.796, 0.545),  # nord13 yellow
    "expired": (0.749, 0.380, 0.416),  # nord11 red
    "none": (0.600, 0.682, 0.749),  # nord9 blue-grey
    "error": (0.749, 0.380, 0.416),
}

# CSS colours for GTK widgets (labels, dots).
STATE_CSS = {
    "valid": "#a3be8c",
    "expiring": "#ebcb8b",
    "expired": "#bf616a",
    "none": "#8fbcbb",
    "error": "#bf616a",
}


def draw_key(cr: cairo.Context, size: int, color: tuple[float, float, float], alpha: float = 1.0) -> None:
    """A key glyph that stays legible at 16 px: ring bow, shaft, two teeth."""
    s = float(size)
    # Measured so the drawn extent (0.078..0.942 x, 0.245..0.718 y) is optically
    # centred rather than mathematically centred — the bowl is heavier than the
    # teeth, so equal bounds still look high.
    cy = 0.455
    cr.set_source_rgba(color[0], color[1], color[2], alpha)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Bow (the ring you hold).
    cr.set_line_width(s * 0.115)
    cr.new_sub_path()
    cr.arc(s * 0.30, s * cy, s * 0.165, 0, 2 * 3.141592653589793)
    cr.stroke()

    # Shaft.
    cr.set_line_width(s * 0.115)
    cr.move_to(s * 0.455, s * cy)
    cr.line_to(s * 0.885, s * cy)
    cr.stroke()

    # Teeth.
    cr.set_line_width(s * 0.10)
    cr.move_to(s * 0.705, s * cy)
    cr.line_to(s * 0.705, s * 0.675)
    cr.stroke()
    cr.set_line_width(s * 0.088)
    cr.move_to(s * 0.835, s * cy)
    cr.line_to(s * 0.835, s * 0.615)
    cr.stroke()


def surface_for_state(state: str, size: int = 22) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    color = STATE_COLORS.get(state, STATE_COLORS["none"])
    if state == "none":
        draw_key(cr, size, color, alpha=0.55)
    elif state in ("expired", "error"):
        draw_key(cr, size, color)
        # A slash reads as "invalid" even at 16 px, where colour alone is weak.
        cr.set_source_rgba(color[0], color[1], color[2], 1.0)
        cr.set_line_width(size * 0.085)
        cr.move_to(size * 0.20, size * 0.80)
        cr.line_to(size * 0.80, size * 0.20)
        cr.stroke()
    else:
        draw_key(cr, size, color)
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


def write_png(path: str, size: int = 256, state: str = "valid") -> str:
    """Render an app icon PNG (used by tools/make-icons.py, not at runtime)."""
    surface = surface_for_state(state, size)
    surface.write_to_png(path)
    return path
