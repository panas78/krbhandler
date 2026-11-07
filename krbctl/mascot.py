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

# Below this the mouth opening is a dark smear under the nose rather than a
# grin. The tongue is drawn only with the mouth: a tongue with no mouth to come
# out of is a red drip hanging off the chin, which is exactly what the small
# launcher sizes showed.
MOUTH_MIN = 26


def _detail_for(size: float) -> int:
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


# Mouth geometry, stated as the edges the shape actually reaches.
#
# A cubic bezier only touches its control points' neighbourhood: with corners at
# CORNER_Y and both controls at C, the curve's midpoint lands at
# (8 * mid - 2 * corner) / 6, never at C. Specifying the mouth by its *edges*
# and deriving the controls means the teeth can be placed against the real
# opening. The earlier bug was exactly this: the teeth were positioned against
# control-point values the curve never reached, so the upper row sat on the
# muzzle and the lower row on the chin.
MOUTH_LEFT = 0.348
MOUTH_RIGHT = 0.652
MOUTH_CORNER_Y = 0.664  # where the lips meet, at each corner
MOUTH_TOP_EDGE = 0.694  # true highest point of the opening, at the middle
MOUTH_BOTTOM_EDGE = 0.826  # true lowest point, at the middle


def _control_y(edge_y: float) -> float:
    """Control-point y that makes the bezier actually reach ``edge_y``.

    Solving ``edge = (corner + 3c + 3c + corner) / 8`` for ``c``.
    """
    return (8 * edge_y - 2 * MOUTH_CORNER_Y) / 6


def _mouth(cr: cairo.Context) -> None:
    """A wide open grin: a smile arc across the top, a heavy jaw underneath."""
    top = _control_y(MOUTH_TOP_EDGE)
    bottom = _control_y(MOUTH_BOTTOM_EDGE)
    cr.move_to(MOUTH_LEFT, MOUTH_CORNER_Y)
    cr.curve_to(0.420, top, 0.580, top, MOUTH_RIGHT, MOUTH_CORNER_Y)
    cr.curve_to(0.628, bottom, 0.372, bottom, MOUTH_LEFT, MOUTH_CORNER_Y)
    cr.close_path()


def _tongue(cr: cairo.Context) -> None:
    """Lolling out of the mouth and over the jaw — the comedy of the face.

    Deliberately off-centre: a symmetric tongue reads as a beard, while an
    asymmetric one reads as a dopey dog.
    """
    cr.move_to(0.378, 0.752)
    cr.curve_to(0.338, 0.858, 0.350, 0.944, 0.402, 0.958)
    cr.curve_to(0.458, 0.972, 0.490, 0.898, 0.486, 0.782)
    cr.curve_to(0.454, 0.770, 0.414, 0.764, 0.378, 0.752)
    cr.close_path()


def _teeth(cr: cairo.Context) -> None:
    """Both rows of teeth, as oversized triangles reaching past the lips.

    Drawing them larger than the opening and letting the caller clip is the
    whole point: their wide base is trimmed by the lip curve, so each tooth
    emerges from the mouth instead of being a triangle placed at coordinates
    that only line up at one x position. That was the bug — fixed coordinates
    inside a curved opening put the top row on the muzzle and the bottom row
    on the chin.
    """
    span = MOUTH_RIGHT - MOUTH_LEFT
    above = MOUTH_CORNER_Y - 0.12  # safely outside the lip, for the clip
    lip_inset = STROKE * 0.45  # the stroke covers this much of the opening

    def tooth(centre: float, half: float, tip_y: float, tip_half: float) -> None:
        """A blunt tapered tooth. Pointed teeth read as fangs; flat tips are
        what make a cartoon dog look silly rather than dangerous."""
        cr.move_to(centre - half, above)
        cr.line_to(centre + half, above)
        cr.line_to(centre + tip_half, tip_y)
        cr.line_to(centre - tip_half, tip_y)
        cr.close_path()

    # Upper row only, three broad teeth. A lower row would sit exactly where
    # the tongue hangs, and two rows pointing at each other read as a maw.
    tip_y = MOUTH_TOP_EDGE + lip_inset + 0.034
    for index in range(3):
        centre = MOUTH_LEFT + span * (0.25 + index * 0.25)
        tooth(centre, span * 0.145, tip_y, span * 0.075)


# ── painter ──────────────────────────────────────────────────────────────────
def draw_face(
    cr: cairo.Context,
    palette: dict,
    *,
    detail: int = DETAIL_FULL,
    monochrome: Optional[tuple[float, float, float]] = None,
    pixel_size: float = 1e9,
) -> None:
    """Draw the face into ``cr`` in unit coordinates.

    ``monochrome`` collapses the whole face to one colour — used by the tray,
    where the colour carries the ticket state and the holes do the drawing.

    ``pixel_size`` is how big this face will actually be drawn, in device
    pixels: it decides which features survive, because whether a mouth reads as
    a grin or as dirt depends on the size, not on the caller.
    """
    show_mouth = pixel_size >= MOUTH_MIN
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

        # The pupil is never optional. Without it a small icon is a blank
        # white eye, which reads as something dead rather than something dopey.
        # It grows as the face shrinks, so it survives the anti-aliasing.
        pupil_r = 0.045 if detail >= 1 else 0.068
        cr.new_sub_path()
        cr.arc(cx + pupil_dx, 0.388, pupil_r, 0, math.tau)
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

    # Mouth, then the teeth clipped to it, then the lip drawn over the seam.
    # Clipping is what keeps the teeth inside the grin; see _teeth.
    cr.set_line_width(STROKE * 0.9)
    if monochrome is None:
        _mouth(cr)
        cr.set_source_rgb(*palette["mouth"])
        cr.fill_preserve()

        if detail >= 2:
            # Path is consumed by clip(), so it is rebuilt for the stroke below.
            cr.save()
            cr.clip()
            _teeth(cr)
            cr.set_source_rgb(*palette["teeth"])
            cr.fill()
            cr.restore()

        cr.new_path()
        _mouth(cr)
        cr.set_source_rgb(*outline)
        cr.stroke()
    elif show_mouth:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        _scaled(cr, lambda: _mouth(cr), sx=1.16, sy=1.24, dy=0.004)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

    # Tongue last, so it sits over the mouth and past the jaw.
    if not show_mouth:
        return
    if monochrome is None:
        fill_stroke(
            lambda: _scaled(cr, lambda: _tongue(cr), sx=0.90, dx=-0.030),
            palette["tongue"],
            STROKE * 0.75,
        )
    else:
        cr.set_line_width(STROKE * 0.70)
        _scaled(cr, lambda: _tongue(cr), sx=1.30, sy=0.78, dy=0.046)
        cr.set_source_rgb(*monochrome)
        cr.fill_preserve()
        cr.set_source_rgb(*outline)
        cr.stroke()
    if detail >= 2 and monochrome is None:
        cr.set_line_width(STROKE * 0.42)
        cr.move_to(0.408, 0.778)
        cr.curve_to(0.386, 0.836, 0.390, 0.891, 0.408, 0.914)
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
    draw_face(cr, FULL, detail=_detail_for(size), monochrome=monochrome, pixel_size=size)
    surface.flush()
    return surface


# ── launcher icon ────────────────────────────────────────────────────────────
# At and above this size the three-headed arrangement is legible; below it,
# a single head is used instead.
THREE_HEAD_MIN = 32

SLAB_TOP = (0.247, 0.278, 0.345)
SLAB_BOTTOM = (0.169, 0.192, 0.239)


def draw_plate(
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    color: tuple[float, float, float],
) -> None:
    """A flat rounded plate — the state-coloured backing behind the tray dog."""
    radius = min(width, height) * 0.30
    _rounded_rect(cr, x, y, width, height, radius)
    cr.set_source_rgb(*color)
    cr.fill()


def draw_slab(cr: cairo.Context, size: float) -> None:
    """The rounded slate plate every launcher icon sits on.

    It exists because the artwork is drawn with near-black outlines: on a dark
    launcher those would vanish without it, and on a light one the pale grey
    body would. Shared so the drawn fallback and the real artwork cannot drift.
    """
    radius = size * 0.22
    _rounded_rect(cr, 0, 0, size, size, radius)
    gradient = cairo.LinearGradient(0, 0, size * 0.35, size)
    gradient.add_color_stop_rgb(0.0, *SLAB_TOP)
    gradient.add_color_stop_rgb(1.0, *SLAB_BOTTOM)
    cr.set_source(gradient)
    cr.fill()


def app_icon_surface(size: int = 256) -> cairo.ImageSurface:
    """Three drawn heads on the slab — the fallback when no artwork is present."""
    surface = _surface(size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    draw_slab(cr, size)

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
        # Detail follows the head's own pixel size — a side head is barely half
        # the slab wide, so judging detail by the slab would over-draw it.
        head_px = size * scale
        cr.save()
        cr.translate(size * (0.5 + offset_x), size * (0.5 + offset_y))
        cr.rotate(angle * 0.5)
        cr.scale(head_px, head_px)
        cr.translate(-0.5, -0.5)
        draw_face(cr, FULL, detail=_detail_for(head_px), pixel_size=head_px)
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
