"""Icon tests — the pipeline, not the aesthetics.

The failures worth guarding against are the silent ones:

* an icon that renders as a flat block, so the tray shows a featureless square;
* a wrong byte order, so a valid green ticket looks red;
* the state colour silently not being applied, so the icon conveys nothing;
* the artwork going missing from the shipped payload.

Cairo's ARGB32 is native-endian: on x86 the bytes per pixel are B, G, R, A, so
alpha is byte 3 and colours must be read in reverse.
"""

from __future__ import annotations

import cairo
import pytest

from krbctl import icons


def alpha_map(surface: cairo.ImageSurface) -> list[int]:
    return list(bytes(surface.get_data())[3::4])


def ink_fraction(surface: cairo.ImageSurface) -> float:
    alphas = alpha_map(surface)
    return sum(1 for value in alphas if value > 8) / len(alphas)


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


def distinct_colours(surface: cairo.ImageSurface) -> int:
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    return len(
        {
            (data[y * stride + x * 4], data[y * stride + x * 4 + 1], data[y * stride + x * 4 + 2])
            for y in range(surface.get_height())
            for x in range(surface.get_width())
            if data[y * stride + x * 4 + 3] > 240
        }
    )


class TestArtwork:
    """The single PNG every icon is built from."""

    def test_artwork_is_present(self):
        """It ships in the payload and is what the icons render from."""
        assert icons.ARTWORK.exists(), f"missing artwork: {icons.ARTWORK}"

    def test_artwork_has_transparency(self):
        """The background must be transparent, or the plate behind it is moot."""
        art = icons.artwork_surface()
        assert art is not None
        transparent = sum(1 for value in alpha_map(art) if value < 8)
        assert transparent > len(alpha_map(art)) * 0.10, "background is not transparent"

    def test_artwork_keeps_interior_whites(self):
        """The teeth and eye highlights must survive; only the background went.

        A luminance key would have eaten them and left a hollowed-out dog.
        """
        art = icons.artwork_surface()
        assert art is not None
        data = bytes(art.get_data())
        stride = art.get_stride()
        bright = 0
        for y in range(0, art.get_height(), 2):
            for x in range(0, art.get_width(), 2):
                index = y * stride + x * 4
                if data[index + 3] > 250 and min(data[index : index + 3]) > 200:
                    bright += 1
        assert bright > 200, "no white detail survived — the artwork looks hollowed out"

    def test_artwork_is_loaded_once(self):
        """Re-decoding an 800 px PNG on every tray refresh would be wasteful."""
        assert icons.artwork_surface() is icons.artwork_surface()


class TestStateColours:
    @pytest.mark.parametrize("state", sorted(icons.STATE_COLORS))
    def test_state_colour_is_on_the_plate(self, state):
        """The plate behind the dog carries the state; sample it off-centre.

        Sampling the icon's first opaque pixel is not meaningful — that is the
        dog, or an antialiased corner. The plate is the band between its own
        inset and the artwork's.
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

    @pytest.mark.parametrize("size", [16, 22, 24, 48])
    def test_sizes_are_square_and_requested(self, size):
        surface = icons.surface_for_state("valid", size)
        assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_icon_is_not_a_flat_block(self):
        """The tray is a few pixels of colour: it only speaks because the plate
        and the dog differ from each other."""
        assert distinct_colours(icons.surface_for_state("valid", 22)) > 20

    def test_plate_is_rounded(self):
        """A square plate would read as a missing icon, not a badge."""
        surface = icons.surface_for_state("valid", 32)
        assert alpha_map(surface)[0] < 40, "top-left corner should be transparent"


class TestMissingArtwork:
    """A missing artwork file must not crash the tray."""

    def test_tray_still_shows_the_state_colour(self, monkeypatch):
        monkeypatch.setattr(icons, "_artwork", None)
        surface = icons.surface_for_state("valid", 32)
        assert surface.get_width() == 32
        expected = tuple(round(c * 255) for c in icons.STATE_COLORS["valid"])
        got = pixel_at(surface, 16, 4)[:3]
        for value, want in zip(got, expected):
            assert abs(value - want) <= 4

    def test_launcher_still_produces_a_square(self, monkeypatch):
        monkeypatch.setattr(icons, "_artwork", None)
        surface = icons.app_icon_surface(64)
        assert (surface.get_width(), surface.get_height()) == (64, 64)


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


class TestAppIcon:
    @pytest.mark.parametrize("size", [16, 32, 48, 256])
    def test_square(self, size):
        surface = icons.app_icon_surface(size)
        assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_has_a_rounded_opaque_background(self):
        """The artwork has near-black outlines and would vanish on a dark
        launcher without the slab."""
        alphas = alpha_map(icons.app_icon_surface(64))
        opaque = sum(1 for value in alphas if value > 250)
        assert opaque > len(alphas) * 0.80
        assert opaque < len(alphas), "corners should be rounded, not square"

    def test_corner_is_transparent(self):
        assert alpha_map(icons.app_icon_surface(64))[0] == 0

    def test_small_sizes_use_one_head(self):
        """Three heads at a third of the width each is a grey blur below 48 px."""
        assert icons.ARTWORK_MIN >= 48
        art = icons.artwork_surface()
        assert art is not None
        crop = icons._head_crop(art)
        assert crop.get_width() < art.get_width() * 0.5

    def test_head_crop_is_within_the_artwork(self):
        art = icons.artwork_surface()
        assert art is not None
        crop = icons._head_crop(art)
        assert crop.get_width() > 0 and crop.get_height() > 0
        assert crop.get_width() <= art.get_width()
        assert crop.get_height() <= art.get_height()

    def test_icon_contains_the_dog(self):
        """The slab alone would pass every geometry test above."""
        assert distinct_colours(icons.app_icon_surface(128)) > 200


class TestGdkPixbuf:
    def test_pixbuf_matches_the_tray_size(self):
        gi = pytest.importorskip("gi")
        gi.require_version("GdkPixbuf", "2.0")
        pixbuf = icons.gdk_pixbuf_for_state("valid", 22)
        assert pixbuf.get_width() == 22 and pixbuf.get_height() == 22
        assert pixbuf.get_has_alpha()
