"""The tray icon and the status badges in the window.

The ticket state is carried by colour: a rounded plate in the state's colour.
Nothing is loaded from disk and nothing is drawn from artwork, so this module is
size-independent and cannot break because a file went missing.

The launcher icon is separate and static — ``data/icons/hicolor/*/apps/krbctl.png``
is installed by ``install.sh`` and looked up through the icon theme by name.
"""

from __future__ import annotations

import math

import cairo

# The coloured plate's inset from its own edge, as a fraction of the icon size.
PLATE_INSET = 0.055

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

# Alpha for "no ticket" — the plate stays visible but reads as inactive.
STATE_ALPHA = {"none": 0.60}


def _rounded_rect(
    cr: cairo.Context, x: float, y: float, width: float, height: float, radius: float
) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def surface_for_state(state: str, size: int = 22) -> cairo.ImageSurface:
    """A rounded plate in the colour for ``state``.

    A solid plate rather than a glyph: at 22 px on a bar, colour is the only
    thing that survives, and the state is the whole message. The shape is the
    same for every state so the eye reads it as one indicator that changes hue,
    not as five unrelated icons.
    """
    # Resolve once, so an unknown state is treated as "none" in every respect —
    # colour *and* fade. Looking each up separately let an unknown state render
    # at full alpha in the "no ticket" colour, which reads as a live ticket.
    state = state if state in STATE_COLORS else "none"
    color = STATE_COLORS[state]

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    arm = size * PLATE_INSET
    _rounded_rect(cr, arm, arm, size - 2 * arm, size - 2 * arm, (size - 2 * arm) * 0.30)
    cr.set_source_rgb(*color)
    cr.fill()
    surface.flush()

    alpha = STATE_ALPHA.get(state)
    if alpha is None:
        return surface

    faded = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(faded)
    cr.set_source_surface(surface, 0, 0)
    cr.paint_with_alpha(alpha)
    faded.flush()
    return faded


def _argb32_big_endian(surface: cairo.ImageSurface) -> bytes:
    """Cairo gives native-endian premultiplied ARGB32; SNI wants big-endian.

    On little-endian machines that means swapping the R and B bytes of every
    pixel. Getting this wrong produces a blue icon where a red one belongs, so
    it is done explicitly rather than hoped for.
    """
    surface.flush()
    stride = surface.get_stride()
    width, height = surface.get_width(), surface.get_height()
    data = bytes(surface.get_data())
    out = bytearray(width * height * 4)
    # native little-endian cairo ARGB32 = [B, G, R, A]; SNI wants [A, R, G, B].
    # Rows are rebuilt tightly packed: cairo pads the stride to 4 bytes, but
    # SNI's byte array is expected to be exactly width*height*4.
    for y in range(height):
        row = y * stride
        out_row = y * width * 4
        for x in range(width):
            i = row + x * 4
            b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
            j = out_row + x * 4
            out[j], out[j + 1], out[j + 2], out[j + 3] = a, r, g, b
    return bytes(out)


def sni_pixmap(state: str, size: int = 22) -> list[tuple[int, int, bytes]]:
    """One-element list matching SNI's ``a(iiay)`` IconPixmap signature."""
    surface = surface_for_state(state, size)
    return [(size, size, _argb32_big_endian(surface))]


def gdk_pixbuf_for_state(state: str, size: int = 22):
    """A GdkPixbuf for GTK widgets, rendered at the same size as the tray."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    surface = surface_for_state(state, size)
    data = _argb32_big_endian(surface)
    # Re-order to RGBA for GdkPixbuf. Cairo premultiplies; GdkPixbuf wants
    # straight alpha, so undo it.
    rgba = bytearray(len(data))
    for i in range(0, len(data), 4):
        a, r, g, b = data[i], data[i + 1], data[i + 2], data[i + 3]
        if a and a < 255:
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
