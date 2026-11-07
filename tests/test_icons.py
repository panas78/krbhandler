"""Icon tests — the tray indicator and the shipped icon set.

The failures worth guarding against are the silent ones:

* a tray icon that renders as nothing, so the bar shows a gap;
* a wrong byte order, so a valid green ticket looks red;
* the state colour silently not being applied, so the icon conveys nothing;
* the static launcher icon set going missing from the payload.

Cairo's ARGB32 is native-endian: on x86 the bytes per pixel are B, G, R, A, so
alpha is byte 3 and colours must be read in reverse.
"""

from __future__ import annotations

from pathlib import Path

import cairo
import pytest

from krbctl import icons

HICOLOR = Path(icons.__file__).resolve().parent.parent / "data" / "icons" / "hicolor"
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def alpha_map(surface: cairo.ImageSurface) -> list[int]:
    return list(bytes(surface.get_data())[3::4])


def pixel_at(surface: cairo.ImageSurface, x: int, y: int) -> tuple[int, int, int, int]:
    """Straight (r, g, b, a) at a point, un-premultiplied."""
    data = bytes(surface.get_data())
    index = y * surface.get_stride() + x * 4
    b, g, r, a = data[index], data[index + 1], data[index + 2], data[index + 3]
    if a and a < 255:
        r = min(255, round(r * 255 / a))
        g = min(255, round(g * 255 / a))
        b = min(255, round(b * 255 / a))
    return r, g, b, a


class TestStateColours:
    @pytest.mark.parametrize("state", sorted(icons.STATE_COLORS))
    def test_state_colour_is_applied(self, state):
        """The plate itself is the indicator, so its colour must be exact.

        Sampled off-centre: the very centre is inside the plate's rounded body,
        but the corners are transparent and would read as 'nothing drawn'.
        """
        size = 64
        surface = icons.surface_for_state(state, size)
        edge = round(size * icons.PLATE_INSET) + 2

        expected = tuple(round(channel * 255) for channel in icons.STATE_COLORS[state])
        samples = {
            "left": (edge, size // 2),
            "right": (size - 1 - edge, size // 2),
            "top": (size // 2, edge),
            "bottom": (size // 2, size - 1 - edge),
        }
        for where, (x, y) in samples.items():
            got = pixel_at(surface, x, y)[:3]
            for value, want in zip(got, expected):
                assert abs(value - want) <= 4, (
                    f"{state}: plate at {where} is {got}, expected {expected}"
                )

    def test_states_are_visually_distinct(self):
        """No two states may share a colour, or the icon conveys nothing."""
        seen = {}
        for state, colour in icons.STATE_COLORS.items():
            assert colour not in seen, f"{state} duplicates {seen[colour]}"
            seen[colour] = state
        assert len(seen) == len(icons.STATE_COLORS)

    def test_none_state_is_faded(self):
        """'No ticket' must look inactive, not like a live green ticket."""
        surface = icons.surface_for_state("none", 32)
        assert max(alpha_map(surface)) < 255

    def test_unknown_state_falls_back_to_none(self):
        """A state string the tray has not seen must not raise."""
        unknown = icons.surface_for_state("nonsense", 32)
        expected = icons.surface_for_state("none", 32)
        assert bytes(unknown.get_data()) == bytes(expected.get_data())

    @pytest.mark.parametrize("size", [16, 22, 24, 48])
    def test_sizes_are_square_and_requested(self, size):
        surface = icons.surface_for_state("valid", size)
        assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_plate_is_rounded(self):
        """A square plate would read as a missing icon, not a badge."""
        assert alpha_map(icons.surface_for_state("valid", 32))[0] < 40

    def test_plate_is_not_empty(self):
        """A fully transparent icon is a gap in the bar, not a status light."""
        alphas = alpha_map(icons.surface_for_state("valid", 22))
        assert sum(1 for value in alphas if value > 200) > len(alphas) * 0.4


class TestTrayPixmap:
    def test_signature_matches_sni(self):
        pixmap = icons.sni_pixmap("valid", 22)
        assert isinstance(pixmap, list) and len(pixmap) == 1
        width, height, data = pixmap[0]
        assert (width, height) == (22, 22)
        assert isinstance(data, bytes)
        assert len(data) == width * height * 4

    def test_buffer_is_argb_big_endian(self):
        """SNI wants [A, R, G, B]; cairo gives [B, G, R, A]."""
        width, height, data = icons.sni_pixmap("valid", 22)[0]
        assert max(data[0::4]) == 255, "no opaque alpha byte"

        first_opaque = next(
            (data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 4) if data[i] == 255
        )
        _, red, green = first_opaque
        assert green > red, "the green state is not green"

    def test_rows_are_tightly_packed(self):
        """Odd widths would leave cairo's stride padding in the buffer.

        A 22 px surface is padded to 24 px per row, and shipping that padding
        shifts every row of the tray icon sideways.
        """
        size = 22
        _, _, data = icons.sni_pixmap("valid", size)[0]
        assert len(data) == size * size * 4
        surface = icons.surface_for_state("valid", size)
        assert surface.get_stride() >= size * 4

    def test_every_state_produces_a_full_buffer(self):
        for state in icons.STATE_COLORS:
            width, height, data = icons.sni_pixmap(state, 22)[0]
            assert len(data) == width * height * 4
            assert any(data[0::4]), f"{state}: fully transparent pixmap"


class TestLauncherIconSet:
    """The launcher icon is a static asset, so the files must be present."""

    def test_hicolor_tree_exists(self):
        assert HICOLOR.is_dir(), f"missing icon set: {HICOLOR}"

    @pytest.mark.parametrize("size", ICON_SIZES)
    def test_every_size_is_present(self, size):
        path = HICOLOR / f"{size}x{size}" / "apps" / "krbctl.png"
        assert path.is_file(), f"missing {size}px icon"

    def test_icons_are_valid_pngs_of_the_right_size(self):
        for size in ICON_SIZES:
            path = HICOLOR / f"{size}x{size}" / "apps" / "krbctl.png"
            surface = cairo.ImageSurface.create_from_png(str(path))
            assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_icons_are_not_blank(self):
        """A blank icon set would install cleanly and look like a missing app."""
        path = HICOLOR / "256x256" / "apps" / "krbctl.png"
        data = bytes(cairo.ImageSurface.create_from_png(str(path)).get_data())
        colours = {data[i : i + 3] for i in range(0, len(data), 4) if data[i + 3] > 200}
        assert len(colours) > 200, "the icon looks like a flat block"

    def test_icons_have_transparent_corners(self):
        """The launcher icon is a rounded slab, not a filled square."""
        path = HICOLOR / "256x256" / "apps" / "krbctl.png"
        surface = cairo.ImageSurface.create_from_png(str(path))
        assert alpha_map(surface)[0] < 40


class TestGdkPixbuf:
    def test_pixbuf_matches_the_tray_size(self):
        gi = pytest.importorskip("gi")
        gi.require_version("GdkPixbuf", "2.0")
        pixbuf = icons.gdk_pixbuf_for_state("valid", 22)
        assert pixbuf.get_width() == 22 and pixbuf.get_height() == 22
        assert pixbuf.get_has_alpha()
