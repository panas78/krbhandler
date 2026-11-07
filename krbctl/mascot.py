"""The krbctl mascot — a cartoon Cerberus, drawn with cairo.

Inspired by a stock cartoon of the three-headed dog who guards the gates
(which is what "Kerberos" means), but authored here as vector code rather than
shipped as an image, for three reasons:

* a downloaded clipart cannot be used in a public repo — stock previews carry
  a baked-in watermark, and this one certainly did;
* a bitmap can only be tinted by washing it out, whereas the tray needs a
  crisp, per-state colour;
* drawn geometry stays sharp at 16 px and at 512 px, with no source to lose.

Everything is expressed in a unit square (0..1, y down) and the caller scales.
That keeps one description of the face driving the tray icon, the window icon
and the launcher icon.

The face is deliberately front-on. A three-quarter view is prettier at 256 px
but unreadable at 22 px, and the tray is the size that matters most.
"""

from __future__ import annotations

import math
from typing import Optional

import cairo

# ── palettes ─────────────────────────────────────────────────────────────────
# Full colour, matching the warm greys of the reference art.
FULL = {
    "fill": (0.678, 0.631, 0.596),  # warm grey body
    "temple": (0.596, 0.549, 0.518),  # slightly darker where the skull rounds
    "stroke": (0.043, 0.031, 0.027),  # near-black outline
    "muzzle": (0.753, 0.710, 0.675),  # a shade lighter than the skull
    "nose": (0.318, 0.227, 0.204),  # dark brown
    "mouth": (0.204, 0.114, 0.106),  # deep maroon interior
    "tongue": (0.851, 0.510, 0.451),  # rose — pushed a little warmer than life
    "tongue_line": (0.643, 0.325, 0.286),
    "teeth": (1.0, 1.0, 1.0),
    "eye": (1.0, 1.0, 1.0),
    "pupil": (0.106, 0.075, 0.067),
    "catchlight": (1.0, 1.0, 1.0),
    "inner_ear": (0.435, 0.302, 0.286),
}

STROKE = 0.030  # outline weight in unit terms — bold, like the reference

# Detail levels. The tray runs at 22 px, where a 1 px feature is mud, so the
# small sizes drop teeth, catchlights and inner ears rather than render noise.
DETAIL_FULL = 3
DETAIL_SMALL = 1

# Below this many pixels the mouth opening collapses into a dark smear under
# the nose and reads as dirt rather than a grin.
MOUTH_MIN = 26


def _detail_for(size: int) -> int:
    if size >= 96:
        return DETAIL_FULL
    if size >= 48:
        return 2
    if size >= 32:
        return 1
    return 0


# ── geometry ─────────────────────────────────────────────────────────────────
def _head(cr: cairo.Context) -> None:
    """The skull path: wide at the temples, tapering to a soft chin."""
    cr.move_to(0.10, 0.40)
    cr.curve_to(0.115, 0.20, 0.300, 0.115, 0.50, 0.115)
    cr.curve_to(0.700, 0.115, 0.885, 0.20, 0.90, 0.40)
    cr.curve_to(0.92, 0.52, 0.93, 0.58, 0.88, 0.62)
    cr.curve_to(0.84, 0.78, 0.66, 0.82, 0.50, 0.82)
    cr.curve_to(0.34, 0.82, 0.16, 0.78, 0.12, 0.62)
    cr.curve_to(0.07, 0.58, 0.08, 0.52, 0.10, 0.40)
    cr.close_path()


def _ear(cr: cairo.Context, side: str) -> None:
    """A pointy ear, mirrored for the other side."""
    if side == "left":
        cr.move_to(0.140, 0.320)
        cr.curve_to(0.118, 0.170, 0.140, 0.085, 0.196, 0.062)
        cr.curve_to(0.262, 0.082, 0.372, 0.148, 0.440, 0.215)
    else:
        cr.move_to(0.860, 0.320)
        cr.curve_to(0.882, 0.170, 0.860, 0.085, 0.804, 0.062)
        cr.curve_to(0.738, 0.082, 0.628, 0.148, 0.560, 0.215)
    cr.close_path()


def _inner_ear(cr: cairo.Context, side: str) -> None:
    if side == "left":
        cr.move_to(0.188, 0.278)
        cr.curve_to(0.176, 0.185, 0.192, 0.128, 0.226, 0.112)
        cr.curve_to(0.276, 0.132, 0.334, 0.178, 0.372, 0.222)
    else:
        cr.move_to(0.812, 0.278)
        cr.curve_to(0.824, 0.185, 0.808, 0.128, 0.774, 0.112)
        cr.curve_to(0.724, 0.132, 0.666, 0.178, 0.628, 0.222)
    cr.close_path()


def _muzzle(cr: cairo.Context) -> None:
    """The snout: a big rounded mass below the eyes, holding nose and mouth.

    Without this the face is a disc with features on it, which reads as a cat
    or a bear. The protruding muzzle is the dog.
    """
    cr.move_to(0.205, 0.640)
    cr.curve_to(0.205, 0.520, 0.330, 0.468, 0.500, 0.468)
    cr.curve_to(0.670, 0.468, 0.795, 0.520, 0.795, 0.640)
    cr.curve_to(0.795, 0.750, 0.670, 0.805, 0.500, 0.805)
    cr.curve_to(0.330, 0.805, 0.205, 0.750, 0.205, 0.640)
    cr.close_path()


def _nose(cr: cairo.Context) -> None:
    """A big rounded inverted triangle — the strongest "dog" signal."""
    cr.move_to(0.372, 0.515)
    cr.curve_to(0.415, 0.484, 0.585, 0.484, 0.628, 0.515)
    cr.curve_to(0.610, 0.592, 0.556, 0.636, 0.500, 0.636)
    cr.curve_to(0.444, 0.636, 0.390, 0.592, 0.372, 0.515)
    cr.close_path()


def _mouth(cr: cairo.Context) -> None:
    """A wide open grin: a smile arc across the top, a heavy jaw underneath."""
    cr.move_to(0.352, 0.672)
    cr.curve_to(0.420, 0.726, 0.580, 0.726, 0.648, 0.672)
    cr.curve_to(0.632, 0.800, 0.368, 0.800, 0.352, 0.672)
    cr.close_path()


def _tongue(cr: cairo.Context) -> None:
    """Lolling out of the mouth and over the jaw — the comedy of the face.

    Deliberately off-centre: a symmetric tongue reads as a beard, while an
    asymmetric one reads as a dopey dog.
    """
    cr.move_to(0.432, 0.756)
    cr.curve_to(0.392, 0.862, 0.404, 0.948, 0.456, 0.962)
    cr.curve_to(0.512, 0.976, 0.544, 0.902, 0.540, 0.786)
    cr.curve_to(0.508, 0.774, 0.468, 0.768, 0.432, 0.756)
    cr.close_path()


def _teeth(cr: cairo.Context, row: str) -> None:
    """Two white triangles per row — enough to say "grinning", no more."""
    if row == "top":
        for centre in (0.400, 0.500, 0.600):
            cr.move_to(centre - 0.048, 0.686)
            cr.line_to(centre + 0.048, 0.686)
            cr.line_to(centre, 0.752 - abs(centre - 0.500) * 0.34)
            cr.close_path()
    else:
        for centre in (0.440, 0.560):
            cr.move_to(centre - 0.040, 0.788)
            cr.line_to(centre + 0.040, 0.788)
            cr.line_to(centre, 0.730)
            cr.close_path()


# ── painter ──────────────────────────────────────────────────────────────────
def draw_face(
    cr: cairo.Context,
    palette: dict,
    *,
    detail: int = DETAIL_FULL,
    monochrome: Optional[tuple[float, float, float]] = None,
    show_mouth: bool = True,
) -> None:
    """Draw the face into ``cr`` in unit coordinates.

    ``monochrome`` collapses the whole face to one colour — used by the tray,
    where the colour carries the ticket state and the holes do the drawing.
    """
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    outline = monochrome or palette["stroke"]

    def fill_stroke(path_fn, fill, width=STROKE):
        cr.set_line_width(width)
        path_fn()
        cr.set_source_rgb(*fill)
        cr.fill_preserve()
        cr.set_source_rgb(*outline)
        cr.stroke()

    # Ears sit behind the skull.
    for side in ("left", "right"):
        fill_stroke(lambda s=side: _ear(cr, s), monochrome or palette["fill"])
        if detail >= 2 and monochrome is None:
            cr.set_line_width(STROKE * 0.55)
            _inner_ear(cr, side)
            cr.set_source_rgb(*palette["inner_ear"])
            cr.fill()

    # Skull.
    fill_stroke(lambda: _head(cr), monochrome or palette["fill"])

    # Eyes. In monochrome they are punched out, which is what makes the face
    # read at 22 px: two bright holes on a dark bar.
    for cx, pupil_dx in ((0.352, 0.019), (0.648, -0.019)):
        radius = 0.104
        cr.new_sub_path()
        cr.arc(cx, 0.372, radius, 0, math.tau)
        if monochrome is None:
            cr.set_line_width(STROKE * 0.85)
            cr.set_source_rgb(*palette["eye"])
            cr.fill_preserve()
            cr.set_source_rgb(*outline)
            cr.stroke()
        else:
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.fill()
            cr.set_operator(cairo.OPERATOR_OVER)

        if detail >= 1:
            cr.new_sub_path()
            cr.arc(cx + pupil_dx, 0.388, 0.045, 0, math.tau)
            cr.set_source_rgb(*(monochrome or palette["pupil"]))
            cr.fill()
            if detail >= DETAIL_FULL:
                cr.new_sub_path()
                cr.arc(cx + pupil_dx - 0.015, 0.371, 0.016, 0, math.tau)
                cr.set_source_rgb(*palette["catchlight"])
                cr.fill()

    # Muzzle, then nose. In monochrome the snout gets no outline: a stroke
    # there would just break the silhouette into stripes at 22 px.
    if monochrome is None:
        fill_stroke(lambda: _muzzle(cr), palette["muzzle"], STROKE * 0.8)
    else:
        cr.set_line_width(0.0)
        _muzzle(cr)
        cr.set_source_rgb(*monochrome)
        cr.fill()

    # Nose. In monochrome it is punched out like the eyes: a filled nose is
    # invisible against a filled face, and the nose is the clearest dog cue
    # the face has.
    if monochrome is None:
        fill_stroke(lambda: _nose(cr), palette["nose"], STROKE * 0.8)
    else:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        _scaled(cr, lambda: _nose(cr), sx=0.88, sy=0.86, dy=-0.018)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

    # Mouth.
    cr.set_line_width(STROKE * 0.9)
    if monochrome is None:
        _mouth(cr)
        cr.set_source_rgb(*palette["mouth"])
        cr.fill_preserve()
        cr.set_source_rgb(*outline)
        cr.stroke()
    elif show_mouth:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        _scaled(cr, lambda: _mouth(cr), sx=1.16, sy=1.24, dy=0.004)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

    if detail >= 2 and monochrome is None:
        for row in ("top", "bottom"):
            _teeth(cr, row)
            cr.set_source_rgb(*palette["teeth"])
            cr.fill()

    # Tongue last, so it sits over the mouth and past the jaw.
    if monochrome is None:
        fill_stroke(lambda: _tongue(cr), palette["tongue"], STROKE * 0.75)
    else:
        cr.set_line_width(STROKE * 0.70)
        _scaled(cr, lambda: _tongue(cr), sx=1.38, sy=0.76, dy=0.050, dx=-0.008)
        cr.set_source_rgb(*monochrome)
        cr.fill_preserve()
        cr.set_source_rgb(*outline)
        cr.stroke()
    if detail >= 2 and monochrome is None:
        cr.set_line_width(STROKE * 0.42)
        cr.move_to(0.492, 0.782)
        cr.curve_to(0.470, 0.840, 0.474, 0.895, 0.492, 0.918)
        cr.set_source_rgb(*palette["tongue_line"])
        cr.stroke()


def _scaled(cr: cairo.Context, path_fn, *, sx=1.0, sy=1.0, dx=0.0, dy=0.0) -> None:
    """Build ``path_fn``'s path through a scale/offset about the face centre.

    Cheaper than maintaining a second set of proportions for small sizes, and
    it keeps the stroke width scaling with the part, which is what the eye
    expects when a shape shrinks.
    """
    cr.save()
    cr.translate(0.5 + dx, 0.5 + dy)
    cr.scale(sx, sy)
    cr.translate(-0.5, -0.5)
    path_fn()
    cr.restore()


def _surface(size: int) -> cairo.ImageSurface:
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)


def face_surface(size: int, *, monochrome: Optional[tuple[float, float, float]] = None):
    """The face alone, filling the square."""
    surface = _surface(size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    cr.scale(size, size)
    draw_face(
        cr,
        FULL,
        detail=_detail_for(size),
        monochrome=monochrome,
        show_mouth=size >= MOUTH_MIN,
    )
    surface.flush()
    return surface


# ── launcher icon ────────────────────────────────────────────────────────────
# At and above this size the three-headed arrangement is legible; below it,
# a single head is used instead.
THREE_HEAD_MIN = 32

SLAB_TOP = (0.247, 0.278, 0.345)
SLAB_BOTTOM = (0.169, 0.192, 0.239)


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Three heads peering over each other on a rounded slab.

    The slab exists because the mascot is drawn with near-black outlines: on a
    dark launcher they would disappear without it, and on a light one the pale
    grey body would.
    """
    surface = _surface(size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    radius = size * 0.22
    _rounded_rect(cr, 0, 0, size, size, radius)
    gradient = cairo.LinearGradient(0, 0, size * 0.35, size)
    gradient.add_color_stop_rgb(0.0, *SLAB_TOP)
    gradient.add_color_stop_rgb(1.0, *SLAB_BOTTOM)
    cr.set_source(gradient)
    cr.fill()

    detail = DETAIL_FULL if size >= 96 else _detail_for(size)

    if size >= THREE_HEAD_MIN:
        # Two heads behind, tilted outwards, then the centre one in front.
        # Drawing the side heads smaller and lower is what sells "all three".
        heads = (
            (0.46, 0.208, 0.150, 0.30),
            (0.46, -0.208, 0.150, -0.30),
            (0.74, 0.0, 0.010, 0.0),
        )
    else:
        # Below ~32 px the side heads are two pixels wide each and the whole
        # thing reads as a brown smudge. One head, straight on, stays legible.
        heads = ((0.92, 0.0, 0.010, 0.0),)

    for scale, offset_x, offset_y, angle in heads:
        cr.save()
        cr.translate(size * (0.5 + offset_x), size * (0.5 + offset_y))
        cr.rotate(angle * 0.5)
        cr.scale(size * scale, size * scale)
        cr.translate(-0.5, -0.5)
        draw_face(cr, FULL, detail=max(DETAIL_SMALL, detail - 1))
        cr.restore()

    surface.flush()
    return surface


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def preview_sheet(path: str) -> str:
    """All state colours at tray size, plus the launcher icon — for eyeballing."""
    from .icons import STATE_COLORS  # local: icons imports this module

    states = ("valid", "expiring", "expired", "none", "error")
    cell, pad, zoom = 22, 18, 4
    big = cell * zoom
    width = pad + len(states) * (big + pad) + big + pad
    height = big + 2 * pad + 26

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.13, 0.14, 0.18)  # a dark bar, to judge real contrast
    cr.paint()

    for index, state in enumerate(states):
        face = face_surface(cell, monochrome=STATE_COLORS[state])
        x = pad + index * (big + pad)
        cr.save()
        cr.translate(x, pad)
        cr.scale(zoom, zoom)
        cr.set_source_surface(face, 0, 0)
        cr.paint()
        cr.restore()
        cr.set_source_rgb(0.85, 0.87, 0.91)
        cr.select_font_face("sans")
        cr.set_font_size(13)
        cr.move_to(x, pad + big + 18)
        cr.show_text(state)

    app = app_icon_surface(big)
    cr.set_source_surface(app, pad + len(states) * (big + pad), pad)
    cr.paint()
    cr.move_to(pad + len(states) * (big + pad), pad + big + 18)
    cr.show_text("launcher")

    surface.write_to_png(path)
    return path
