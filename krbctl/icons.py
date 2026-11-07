"""Icons for the tray, the window and the launcher.

Two jobs, one picture:

* The **launcher** icon is the Cerberus artwork on a rounded slate plate. The dog
  is the brand there, seen at 48 px and up, so detail is the point.
* The **tray** icon is the same dog on a plate in the ticket-state colour. The
  state cannot be carried by tinting the drawing: at 22 px that either flattens
  it to a silhouette — a shapeless blob — or washes the colours out to grey. A
  solid plate keeps the dog recognisable, reads the same on a light or a dark
  bar, and makes the state obvious at a glance, which is the whole job.

The artwork is a single PNG with a transparent background. Everything else here
is sizing, cropping and colour.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import cairo

ARTWORK = Path(__file__).resolve().parent.parent / "data" / "cerberus.png"

# Below this many pixels three heads side by side merge into a grey blur: in a
# square icon each head only gets a third of the width, which no amount of
# cropping fixes — measured by rendering the artwork, a top band of it, and a
# single head, then looking at each at true size.
ARTWORK_MIN = 48

# How much of the slab the artwork may occupy, as a fraction of its edge.
ARTWORK_INSET = 0.025

# The left head — the goofy one with the tongue out — as a fraction of the
# artwork. Used below ARTWORK_MIN, where one head reads and three do not. It is
# a crop of the same drawing, so the icon keeps one style down to 16 px.
HEAD_BOX = (0.000, 0.030, 0.400, 0.520)

# The coloured plate behind the tray icon, and how much of the tray icon the dog
# occupies on top of it. Both judged at true 22 px on a dark bar.
PLATE_INSET = 0.055
PLATE_ART_INSET = 0.115

# The launcher slab, top and bottom of its vertical gradient.
SLAB_TOP = (0.247, 0.278, 0.345)
SLAB_BOTTOM = (0.169, 0.192, 0.239)

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

# Alpha for "no ticket" — the dog stays visible but reads as inactive.
STATE_ALPHA = {"none": 0.60}

_ARTWORK_UNSET = object()
_artwork: object = _ARTWORK_UNSET


def artwork_surface() -> Optional[cairo.ImageSurface]:
    """The Cerberus artwork, or ``None`` if it is not installed.

    Cached because one tray refresh renders several icons and re-decoding the
    PNG each time is wasteful.
    """
    global _artwork
    if _artwork is not _ARTWORK_UNSET:
        return _artwork
    _artwork = cairo.ImageSurface.create_from_png(str(ARTWORK)) if ARTWORK.exists() else None
    return _artwork


# ── drawing helpers ──────────────────────────────────────────────────────────
def _rounded_rect(
    cr: cairo.Context, x: float, y: float, width: float, height: float, radius: float
) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_plate(cr: cairo.Context, size: float, inset: float, color: tuple[float, float, float]) -> None:
    """A flat rounded plate in the state colour."""
    arm = size * inset
    _rounded_rect(cr, arm, arm, size - 2 * arm, size - 2 * arm, (size - 2 * arm) * 0.30)
    cr.set_source_rgb(*color)
    cr.fill()


def _draw_slab(cr: cairo.Context, size: float) -> None:
    """The rounded slate plate the launcher icon sits on.

    It exists because the artwork is drawn with near-black outlines: on a dark
    launcher those would vanish without it, and on a light one the pale grey body
    would.
    """
    _rounded_rect(cr, 0, 0, size, size, size * 0.22)
    gradient = cairo.LinearGradient(0, 0, size * 0.35, size)
    gradient.add_color_stop_rgb(0.0, *SLAB_TOP)
    gradient.add_color_stop_rgb(1.0, *SLAB_BOTTOM)
    cr.set_source(gradient)
    cr.fill()


def _draw_fitted(cr: cairo.Context, image: cairo.ImageSurface, size: int, inset_frac: float) -> None:
    """Draw ``image`` centred in a ``size`` square, inset by a fraction of it.

    Fits by the longer edge and keeps the aspect, so a wide composition is never
    squashed and a tall crop is never stretched.
    """
    box = size - 2 * (size * inset_frac)
    scale = min(box / image.get_width(), box / image.get_height())
    drawn_w, drawn_h = image.get_width() * scale, image.get_height() * scale
    cr.save()
    cr.translate((size - drawn_w) / 2, (size - drawn_h) / 2)
    cr.scale(scale, scale)
    cr.set_source_surface(image, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    cr.restore()


def _head_crop(art: cairo.ImageSurface) -> cairo.ImageSurface:
    """Just the left head, as its own surface."""
    width, height = art.get_width(), art.get_height()
    fx0, fy0, fx1, fy1 = HEAD_BOX
    x0, y0 = int(fx0 * width), int(fy0 * height)
    x1, y1 = int(fx1 * width), int(fy1 * height)
    crop = cairo.ImageSurface(cairo.FORMAT_ARGB32, x1 - x0, y1 - y0)
    cr = cairo.Context(crop)
    cr.set_source_surface(art, -x0, -y0)
    cr.paint()
    crop.flush()
    return crop


# ── icons ────────────────────────────────────────────────────────────────────
def surface_for_state(state: str, size: int = 22) -> cairo.ImageSurface:
    """The tray icon for ``state``: the dog on a plate in the state colour."""
    color = STATE_COLORS.get(state, STATE_COLORS["none"])
    art = artwork_surface()

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    _draw_plate(cr, size, PLATE_INSET, color)
    if art is not None:
        _draw_fitted(cr, _head_crop(art), size, PLATE_ART_INSET)
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


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Full-colour launcher icon: the artwork on the rounded slab.

    Two compositions, because one cannot work at both ends of the size range:

    * 48 px and up — the whole artwork, three heads, which is the joke.
    * below 48 px — the left head alone. At a third of the width each, three
      heads are a grey blur at 32 px; one head still reads.

    Either way the art is fitted by its longest edge and centred.
    """
    art = artwork_surface()
    image = None if art is None else (art if size >= ARTWORK_MIN else _head_crop(art))

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    _draw_slab(cr, size)
    if image is not None:
        _draw_fitted(cr, image, size, ARTWORK_INSET)
    surface.flush()
    return surface


# ── exports for GTK and for SNI ──────────────────────────────────────────────
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
