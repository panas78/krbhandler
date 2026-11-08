"""The tray icon and the status badges in the window.

Two different jobs, two different renderings:

* **The tray shows the app icon.** On a bar, a recognisable icon is what tells
  you *which* app this is; a coloured square does not. The committed launcher
  PNG is loaded from the icon theme and drawn as-is.
* **The window shows a state plate.** In a list of accounts each row has a name
  next to it, so a solid colour chip reads faster than a picture would.

The state does not disappear from the tray, it stops shouting: the plate colour
becomes a small corner dot that appears only when the state is worth acting on
(expiring, expired, unreadable, or no ticket at all). A healthy ticket is the
quiet case — just the icon.

Every rendered surface is size-independent and falls back to the drawn plate if
the PNG cannot be found, so a missing icon degrades to the old look rather than
to a blank square.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import cairo

# The coloured plate's inset from its own edge, as a fraction of the icon size.
PLATE_INSET = 0.055

# The tray is drawn at whatever size the bar asks for; 22 px is what SNI hosts
# use on this desktop, so it is the one that must look right.
TRAY_SIZE = 22

# Icon theme name, matching the files install.sh drops into hicolor.
ICON_NAME = "krbctl"

# Preferred source sizes, nearest-to-target first. The committed set ships all
# of these, so a bar asking for 22 gets the hand-tuned 22 px PNG rather than a
# downscale of the 512 px one.
ICON_SIZES = (22, 24, 16, 32, 48, 64, 128, 256, 512)

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

# States that earn a dot on the tray icon. "valid" is deliberately absent: a
# ticket that is fine needs no announcement, so the tray stays a plain app icon.
BADGED_STATES = ("expiring", "expired", "error", "none")

# The dot's outer radius and inner colour radius, as fractions of icon size. The
# gap between them is a dark ring so the colour still separates from the busy
# artwork behind it, which a bare dot would not. Tuned by eye on the real 22 px
# icon: 0.26 swallowed the artwork and 0.15 vanished on a dark bar.
DOT_OUTER = 0.18
DOT_INNER = 0.125

# The ring that separates the dot from the artwork behind it.
RING_COLOR = (0.10, 0.12, 0.16)

_icon_cache: dict[tuple[str, int], cairo.ImageSurface | None] = {}


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


def _icon_base_dirs() -> list[Path]:
    """Every ``…/share/icons`` directory the icon theme spec searches."""
    bases: list[Path] = []
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    bases.append(Path(data_home) / "icons")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for entry in data_dirs.split(":"):
        if entry:
            bases.append(Path(entry) / "icons")
    return bases


def _sizes_from(size: int) -> list[int]:
    """``ICON_SIZES`` ordered by distance from ``size``, nearest first."""
    return sorted(ICON_SIZES, key=lambda candidate: (abs(candidate - size), -candidate))


def app_icon_path(size: int = TRAY_SIZE) -> Path | None:
    """The installed ``krbctl.png`` closest to ``size``, or None if absent.

    Resolved through the icon theme search path rather than a path relative to
    the package: ``install.sh`` deliberately keeps no PNGs in the payload, so
    the theme directory is the only place the icon is guaranteed to be.
    """
    override = os.environ.get("KRBCTL_ICON_PATH")
    if override:
        path = Path(override)
        return path if path.is_file() else None

    for base in _icon_base_dirs():
        for candidate in _sizes_from(size):
            path = base / "hicolor" / f"{candidate}x{candidate}" / "apps" / f"{ICON_NAME}.png"
            if path.is_file():
                return path
    return None


def _app_icon_surface(size: int) -> cairo.ImageSurface | None:
    """The app icon at exactly ``size`` px, or None if it cannot be loaded.

    Cached per (file, size): the tray redraws on every poll, and decoding a PNG
    twice a minute to produce the same pixels is pointless.
    """
    path = app_icon_path(size)
    if path is None:
        return None

    key = (str(path), size)
    if key in _icon_cache:
        return _icon_cache[key]

    try:
        source = cairo.ImageSurface.create_from_png(str(path))
    except (cairo.Error, OSError):
        _icon_cache[key] = None
        return None

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    # Scale the source to fill the target. The committed sizes match what a bar
    # asks for, so in practice this is 1:1 and no resampling happens.
    cr.scale(size / source.get_width(), size / source.get_height())
    cr.set_source_surface(source, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    surface.flush()

    _icon_cache[key] = surface
    return surface


def clear_icon_cache() -> None:
    """Drop cached decodes — for tests, and after an icon set is reinstalled."""
    _icon_cache.clear()


def _draw_state_dot(cr: cairo.Context, state: str, size: int) -> None:
    """A ringed dot in the state colour, bottom-right of the icon."""
    outer = size * DOT_OUTER
    inner = size * DOT_INNER
    centre = size - outer - size * 0.05

    cr.arc(centre, centre, outer, 0, 2 * math.pi)
    cr.set_source_rgb(*RING_COLOR)
    cr.fill()

    cr.arc(centre, centre, inner, 0, 2 * math.pi)
    cr.set_source_rgb(*STATE_COLORS[state])
    cr.fill()


def tray_surface_for_state(state: str, size: int = TRAY_SIZE) -> cairo.ImageSurface:
    """The app icon, with a state dot when the state is worth acting on.

    Falls back to the plain state plate when no icon PNG can be found, so the
    tray is never a blank square and an uninstalled icon set is visible rather
    than silent.
    """
    state = state if state in STATE_COLORS else "none"

    icon = _app_icon_surface(size)
    if icon is None:
        return surface_for_state(state, size)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    cr.set_source_surface(icon, 0, 0)
    cr.paint()

    if state in BADGED_STATES:
        _draw_state_dot(cr, state, size)

    surface.flush()
    return surface


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


def sni_pixmap(state: str, size: int = TRAY_SIZE) -> list[tuple[int, int, bytes]]:
    """One-element list matching SNI's ``a(iiay)`` IconPixmap signature."""
    surface = tray_surface_for_state(state, size)
    return [(size, size, _argb32_big_endian(surface))]


def gdk_pixbuf_for_state(state: str, size: int = TRAY_SIZE):
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
