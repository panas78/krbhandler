"""Icons for the tray, the window and the launcher.

The artwork lives in :mod:`krbctl.mascot` as cairo code — a cartoon Cerberus,
the three-headed dog who guards the gates. That means:

* no external file to lose, and no licence question about a downloaded image;
* one description of the face drives the tray, the window and the launcher, so
  they cannot drift apart;
* any size, sharp.

The tray shows the face as a *monochrome* silhouette punched with holes for the
eyes, nose and mouth. Two reasons: the colour then carries the ticket state,
which is the whole point of a tray icon, and holes read far better than fills
at 22 px on a dark bar.

The launcher icon is the full-colour version, because there the dog is the
brand rather than a status light.
"""

from __future__ import annotations

import os

import cairo

from . import mascot

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

# Alpha for "no ticket" — the face stays visible but reads as inactive.
STATE_ALPHA = {"none": 0.60}


def surface_for_state(state: str, size: int = 22) -> cairo.ImageSurface:
    """A square ARGB surface with the mascot in the colour for ``state``."""
    color = STATE_COLORS.get(state, STATE_COLORS["none"])
    face = mascot.face_surface(size, monochrome=color)

    alpha = STATE_ALPHA.get(state)
    if alpha is None:
        return face

    faded = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(faded)
    cr.set_source_surface(face, 0, 0)
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


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Full-colour launcher icon: the mascot on a rounded slab."""
    return mascot.app_icon_surface(size)


def write_png(path: str, size: int = 256, state: str = "valid", *, app_icon: bool = True) -> str:
    """Render an icon PNG. Used by tools/make-icons.py, not at runtime."""
    surface = app_icon_surface(size) if app_icon else surface_for_state(state, size)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    surface.write_to_png(path)
    return path


def preview_sheet(path: str) -> str:
    """Every state at true tray size plus the launcher icon, for eyeballing."""
    return mascot.preview_sheet(path)

