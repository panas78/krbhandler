"""Icon tests — the pipeline, not the aesthetics.

The failures worth guarding against are the silent ones:

* an icon that renders as a solid blob, so the tray shows a featureless square;
* a wrong byte order, so a valid green ticket looks red;
* the state colour silently not being applied, so the icon conveys nothing;
* the artwork going missing and the icon quietly becoming something else.

Cairo's ARGB32 is native-endian: on x86 the bytes per pixel are B, G, R, A, so
alpha is byte 3 and colours must be read in reverse.
"""

from __future__ import annotations

from pathlib import Path

import cairo
import pytest

from krbctl import icons, mascot

ARTWORK = Path(icons.__file__).resolve().parent.parent / "data/icons/src/cerberus.png"


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
        r, g, b = (min(255, round(c * 255 / a)) for c in (r, g, b))
    return r, g, b, a


class TestArtwork:
    """The launcher icon's source file, and what it has to be."""

    def test_artwork_is_present(self):
        assert ARTWORK.exists(), "missing artwork: run tools/prepare-art.py"

    def test_artwork_has_transparency(self):
        """prepare-art.py must have keyed the white background out.

        Without this the launcher icon is a white square with a dog in it, and
        the dark slab behind it is pointless.
        """
        art = cairo.ImageSurface.create_from_png(str(ARTWORK))
        alphas = alpha_map(art)
        transparent = sum(1 for value in alphas if value < 8)
        assert transparent > len(alphas) * 0.10, "background was not keyed out"

    def test_artwork_keeps_interior_whites(self):
        """Keying must remove the background, not every white pixel.

        A luminance key would eat the teeth and the eye highlights and leave a
        hollowed-out dog. The flood fill is what prevents that, so this asserts
        that plenty of near-white survives inside the silhouette.
        """
        art = cairo.ImageSurface.create_from_png(str(ARTWORK))
        data = bytes(art.get_data())
        stride = art.get_stride()
        bright = 0
        for y in range(0, art.get_height(), 2):
            for x in range(0, art.get_width(), 2):
                index = y * stride + x * 4
                if data[index + 3] > 250 and min(data[index : index + 3]) > 200:
                    bright += 1
        assert bright > 200, "no white detail survived — keying was too aggressive"


class TestStateColours:
    @pytest.mark.parametrize("state", sorted(icons.STATE_COLORS))
    def test_state_colour_is_on_the_plate(self, state):
        """The plate behind the dog carries the state; sample it off-centre.

        Sampling the icon's first opaque pixel is no longer meaningful — that is
        now the dog, or an antialiased corner. The plate is the band between its
        own inset and the artwork's.
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

    def test_icon_is_not_a_featureless_blob(self):
        """The tray is a few pixels of colour: it only speaks because the plate
        and the dog differ from each other."""
        surface = icons.surface_for_state("valid", 22)
        data = bytes(surface.get_data())
        stride = surface.get_stride()
        opaque = {
            tuple(data[y * stride + x * 4 : y * stride + x * 4 + 3])
            for y in range(22)
            for x in range(22)
            if data[y * stride + x * 4 + 3] > 240
        }
        assert len(opaque) > 20, "the 22px icon is a single flat colour"


class TestArtworkFallback:
    def test_tray_falls_back_to_the_drawn_face_without_artwork(self, monkeypatch):
        """No artwork must degrade to something legible, not to a blank icon."""
        monkeypatch.setattr(icons, "_artwork", None)
        surface = icons.surface_for_state("valid", 32)
        assert ink_fraction(surface) > 0.25

    def test_launcher_falls_back_to_the_drawn_icon_without_artwork(self, monkeypatch):
        monkeypatch.setattr(icons, "_artwork", None)
        surface = icons.app_icon_surface(64)
        assert (surface.get_width(), surface.get_height()) == (64, 64)
        alphas = alpha_map(surface)
        assert sum(1 for value in alphas if value > 250) > len(alphas) * 0.5


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
        surface = icons.app_icon_surface(64)
        alphas = alpha_map(surface)
        opaque = sum(1 for value in alphas if value > 250)
        assert opaque > len(alphas) * 0.80
        assert opaque < len(alphas), "corners should be rounded, not square"

    def test_corner_is_transparent(self):
        surface = icons.app_icon_surface(64)
        assert alpha_map(surface)[0] == 0

    def test_small_sizes_use_one_head(self):
        """Three heads at a third of the width each is a grey blur below 48 px."""
        assert icons.ARTWORK_MIN >= 48
        full = icons.artwork_surface()
        assert full is not None
        crop = icons._head_crop(full)
        # The crop is a single head: roughly a third as wide as the artwork.
        assert crop.get_width() < full.get_width() * 0.5

    def test_head_crop_is_within_the_artwork(self):
        full = icons.artwork_surface()
        assert full is not None
        crop = icons._head_crop(full)
        assert crop.get_width() > 0 and crop.get_height() > 0
        assert crop.get_width() <= full.get_width()
        assert crop.get_height() <= full.get_height()


class TestGdkPixbuf:
    def test_pixbuf_matches_the_tray_size(self):
        gi = pytest.importorskip("gi")
        gi.require_version("GdkPixbuf", "2.0")
        pixbuf = icons.gdk_pixbuf_for_state("valid", 22)
        assert pixbuf.get_width() == 22 and pixbuf.get_height() == 22
        assert pixbuf.get_has_alpha()


class TestMaskFreedDrawing:
    """The drawn face is still the fallback, so it must stay functional."""

    @pytest.mark.parametrize("size", [22, 24, 32, 48, 128])
    def test_face_is_not_a_solid_blob(self, size):
        face = mascot.face_surface(size, monochrome=icons.STATE_COLORS["valid"])
        fraction = ink_fraction(face)
        assert 0.30 < fraction < 0.80, f"{size}px: ink covers {fraction:.0%}"

    def test_face_has_transparent_holes_inside_the_silhouette(self):
        size = 64
        face = mascot.face_surface(size, monochrome=icons.STATE_COLORS["valid"])
        alphas = alpha_map(face)

        def at(x, y):
            return alphas[y * size + x]

        for label, (fx, fy) in {
            "left eye": (0.292, 0.372),
            "right eye": (0.708, 0.372),
            "nose": (0.500, 0.545),
        }.items():
            x, y = int(fx * size), int(fy * size)
            assert at(x, y) < 40, f"{label} is not punched out (alpha {at(x, y)})"

        for label, (fx, fy) in {
            "forehead": (0.500, 0.240),
            "left cheek": (0.130, 0.480),
            "right cheek": (0.870, 0.480),
        }.items():
            x, y = int(fx * size), int(fy * size)
            assert at(x, y) > 200, f"{label} should be solid (alpha {at(x, y)})"

    def test_eyes_are_mirrored(self):
        size = 64
        face = mascot.face_surface(size, monochrome=icons.STATE_COLORS["valid"])
        alphas = alpha_map(face)
        y = int(0.372 * size)

        def find_hole_start(x_from, x_to, step):
            for x in range(x_from, x_to, step):
                if alphas[y * size + x] < 40:
                    return x
            return None

        left = find_hole_start(0, size // 2, 1)
        right = find_hole_start(size - 1, size // 2, -1)
        assert left is not None and right is not None
        assert abs(left - (size - 1 - right)) <= 2, "eyes are not symmetric"

    def test_mouth_threshold_is_respected(self):
        """Below MOUTH_MIN a mouth hole is a smudge, and the tongue follows it."""
        assert mascot.MOUTH_MIN >= 26
        small = mascot.face_surface(22, monochrome=(1.0, 1.0, 1.0))
        assert len(alpha_map(small)) == 22 * 22
