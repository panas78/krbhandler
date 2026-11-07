"""Icons for the tray, the window and the launcher.

Two different jobs, so two different pictures:

* The **launcher** icon is the full-colour Cerberus artwork on the slate slab.
  There the dog is the brand, seen at 48 px and up, and detail is the point.
* The **tray** icon is a drawn monochrome face, punched with holes for the eyes,
  nose and mouth. At 22 px on a dark bar the artwork would be a grey smudge, and
  the colour has to carry the ticket state — which is the entire reason a tray
  icon exists. :mod:`krbctl.mascot` draws that face in code so it stays sharp at
  every size.

So the artwork is for looking at, and the drawn face is for glancing at.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import cairo

from . import mascot

# Below this many pixels three heads side by side merge into a grey blur: in a
# square icon each head only gets a third of the width, which no amount of
# cropping fixes — measured by rendering the artwork, a top band of it, and a
# single head, then looking at each at true size.
ARTWORK_MIN = 48

# How much of the slab the artwork may occupy, as a fraction of its edge. The
# artwork already carries its own margin from prepare-art.py, so this is small.
ARTWORK_INSET = 0.025

# The left head — the goofy one with the tongue out — as a fraction of the
# artwork. Used below ARTWORK_MIN, where one head reads and three do not. It is
# a crop of the same drawing rather than the code-drawn face so the icon keeps
# one style all the way down to 16 px.
HEAD_BOX = (0.000, 0.030, 0.400, 0.520)

# The coloured plate behind the tray icon, and how much of the tray icon the
# dog occupies on top of it. Both judged at true 22 px on a dark bar.
PLATE_INSET = 0.055
PLATE_ART_INSET = 0.115

_ARTWORK_UNSET = object()
_artwork: object = _ARTWORK_UNSET

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
    """The tray icon for ``state``: the dog on a plate in the state colour.

    The state is carried by the plate rather than by tinting the dog. Tinting a
    detailed drawing either flattens it to a silhouette — which at 22 px is a
    shapeless blob — or washes the colours out until it is a grey smudge. A
    solid plate behind the artwork keeps the dog recognisable, works on a light
    or a dark bar alike, and makes the state obvious at a glance, which is the
    whole job of this icon.
    """
    color = STATE_COLORS.get(state, STATE_COLORS["none"])
    art = artwork_surface()

    if art is None:  # no artwork installed: fall back to the drawn face
        face = mascot.face_surface(size, monochrome=color)
    else:
        face = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(face)
        cr.set_antialias(cairo.ANTIALIAS_BEST)

        arm = size * PLATE_INSET
        mascot.draw_plate(cr, arm, arm, size - 2 * arm, size - 2 * arm, color)
        _draw_fitted(cr, _head_crop(art), size, PLATE_ART_INSET)

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


def artwork_surface() -> Optional[cairo.ImageSurface]:
    """The prepared Cerberus artwork, or ``None`` if it is not installed.

    ``tools/prepare-art.py`` keys the white background out and crops it; this
    loads the result. Cached because the icon set renders nine sizes in a row
    and decoding an 800 px PNG nine times is wasteful.
    """
    global _artwork
    if _artwork is not _ARTWORK_UNSET:
        return _artwork
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "icons" / "src" / "cerberus.png"
    _artwork = cairo.ImageSurface.create_from_png(str(path)) if path.exists() else None
    return _artwork


def _draw_fitted(
    cr: cairo.Context,
    image: cairo.ImageSurface,
    size: int,
    inset_frac: float,
) -> None:
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


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Full-colour launcher icon: the artwork on the rounded slab.

    Two compositions, because one cannot work at both ends of the size range:

    * 48 px and up — the whole artwork, three heads, which is the joke.
    * below 48 px — the left head alone, cropped from the same drawing. At a
      third of the width each, three heads are a grey blur at 32 px; one head
      still reads, and cropping rather than redrawing keeps the style identical.

    Either way the art is fitted by its longest edge and centred.
    """
    art = artwork_surface()
    if art is None:
        return mascot.app_icon_surface(size)  # no artwork installed

    image = art if size >= ARTWORK_MIN else _head_crop(art)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    mascot.draw_slab(cr, size)

    _draw_fitted(cr, image, size, ARTWORK_INSET)
    surface.flush()
    return surface


def write_png(path: str, size: int = 256, state: str = "valid", *, app_icon: bool = True) -> str:
    """Render an icon PNG. Used by tools/make-icons.py, not at runtime."""
    surface = app_icon_surface(size) if app_icon else surface_for_state(state, size)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    surface.write_to_png(path)
    return path


def preview_sheet(path: str) -> str:
    """Every state at true tray size plus the launcher icon, for eyeballing.

    Tray icons are drawn at 22 px and shown at 4x: judging them at any other
    size is how you end up shipping something that looks fine here and is mud
    in the bar.
    """
    pad, zoom = 18, 4
    cell = 22
    big = cell * zoom
    states = tuple(STATE_COLORS)
    width = pad + (len(states) + 1) * (big + pad)
    height = big + 2 * pad + 26

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.13, 0.14, 0.18)  # a dark bar, to judge real contrast
    cr.paint()
    cr.select_font_face("sans")
    cr.set_font_size(13)

    def label(text: str, x: int) -> None:
        cr.set_source_rgb(0.85, 0.87, 0.91)
        cr.move_to(x, pad + big + 18)
        cr.show_text(text)

    for index, state in enumerate(states):
        face = surface_for_state(state, cell)
        x = pad + index * (big + pad)
        cr.save()
        cr.translate(x, pad)
        cr.scale(zoom, zoom)
        cr.set_source_surface(face, 0, 0)
        cr.paint()
        cr.restore()
        label(state, x)

    x = pad + len(states) * (big + pad)
    cr.set_source_surface(app_icon_surface(big), x, pad)
    cr.paint()
    label("launcher", x)

    surface.write_to_png(path)
    return path

