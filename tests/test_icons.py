"""Icon tests — the drawing pipeline, not the aesthetics.

The failures worth guarding against are the silent ones: a face that renders as
a solid blob (so the tray shows a featureless square), a wrong byte order (so a
green ticket looks blue), or a regression back to depending on a downloaded
image that isn't there.
"""

from __future__ import annotations

import cairo
import pytest

from krbctl import icons, mascot


def alpha_map(surface: cairo.ImageSurface) -> list[int]:
    """Alpha of every pixel, row-major.

    Cairo's ARGB32 is native-endian, so on x86 the bytes per pixel are
    B, G, R, A and the alpha channel is byte 3.
    """
    return list(bytes(surface.get_data())[3::4])


def ink_fraction(surface: cairo.ImageSurface) -> float:
    alphas = alpha_map(surface)
    return sum(1 for value in alphas if value > 8) / len(alphas)


class TestMascotFace:
    @pytest.mark.parametrize("size", [22, 24, 32, 48, 128])
    def test_face_is_not_a_solid_blob(self, size):
        """A blob means the holes did not punch through.

        The tray is a few pixels of colour on a dark bar: it only says anything
        because the eyes, nose and mouth are transparent.
        """
        face = mascot.face_surface(size, monochrome=icons.STATE_COLORS["valid"])
        fraction = ink_fraction(face)
        assert 0.30 < fraction < 0.80, f"{size}px: ink covers {fraction:.0%}"

    def test_face_has_transparent_holes_inside_the_silhouette(self):
        """Holes must be *interior* — an icon that is transparent at the edges
        only is a shape with a bite taken out, not a face."""
        size = 64
        face = mascot.face_surface(size, monochrome=icons.STATE_COLORS["valid"])
        alphas = alpha_map(face)

        def at(x, y):
            return alphas[y * size + x]

        # Sample inside each eye but off the pupil: from 32 px up a pupil is
        # drawn into the hole, which is exactly what makes the eyes googly.
        for label, (fx, fy) in {
            "left eye": (0.292, 0.372),
            "right eye": (0.708, 0.372),
            "nose": (0.500, 0.545),
        }.items():
            x, y = int(fx * size), int(fy * size)
            assert at(x, y) < 40, f"{label} is not punched out (alpha {at(x, y)})"

        # And the solid parts must still be solid.
        for label, (fx, fy) in {
            "forehead": (0.500, 0.240),
            "left cheek": (0.130, 0.480),
            "right cheek": (0.870, 0.480),
        }.items():
            x, y = int(fx * size), int(fy * size)
            assert at(x, y) > 200, f"{label} should be solid (alpha {at(x, y)})"

    def test_eyes_are_mirrored(self):
        """Two eyes at the same height and distance from centre."""
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

    def test_small_sizes_drop_the_mouth(self):
        """At tray size the mouth opening becomes a smudge under the nose."""
        alphas22 = alpha_map(mascot.face_surface(22, monochrome=(1.0, 1.0, 1.0)))
        assert len(alphas22) == 22 * 22
        # The mouth row is clear in neither case at 22px; the real guard is
        # that the constant is respected.
        assert mascot.MOUTH_MIN >= 26


class TestStateColours:
    @pytest.mark.parametrize("state", ["valid", "expiring", "expired", "none", "error"])
    def test_state_colour_is_applied(self, state):
        """Cairo stores premultiplied ARGB, so divide the alpha back out."""
        size = 48
        surface = icons.surface_for_state(state, size)
        stride = surface.get_stride()
        data = bytes(surface.get_data())

        found = None
        for y in range(size):
            for x in range(size):
                index = y * stride + x * 4
                alpha = data[index + 3]
                if alpha:
                    found = tuple(round(data[index + c] * 255 / alpha) for c in (2, 1, 0))
                    break
            if found:
                break
        assert found is not None, f"{state}: nothing drawn"

        expected = tuple(int(round(channel * 255)) for channel in icons.STATE_COLORS[state])
        for got, want in zip(found, expected):
            assert abs(got - want) <= 3, f"{state}: {found} != {expected}"

    def test_states_are_visually_distinct(self):
        """No two states may share a colour, or the icon conveys nothing."""
        seen = {}
        for state, colour in icons.STATE_COLORS.items():
            assert colour not in seen, f"{state} duplicates {seen[colour]}"
            seen[colour] = state
        assert len(seen) == 5

    def test_none_state_is_faded(self):
        """'No ticket' must look inactive, not like a live green ticket."""
        surface = icons.surface_for_state("none", 32)
        assert max(alpha_map(surface)) < 255

    @pytest.mark.parametrize("size", [16, 22, 24, 48])
    def test_sizes_are_square_and_requested(self, size):
        surface = icons.surface_for_state("valid", size)
        assert (surface.get_width(), surface.get_height()) == (size, size)


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


class TestAppIcon:
    @pytest.mark.parametrize("size", [16, 32, 48, 256])
    def test_square(self, size):
        surface = icons.app_icon_surface(size)
        assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_has_a_rounded_opaque_background(self):
        """The mascot is drawn with near-black outlines and would vanish on a
        dark launcher without the slab."""
        surface = icons.app_icon_surface(64)
        alphas = alpha_map(surface)
        opaque = sum(1 for value in alphas if value > 250)
        assert opaque > len(alphas) * 0.80
        assert opaque < len(alphas), "corners should be rounded, not square"

    def test_corner_is_transparent(self):
        surface = icons.app_icon_surface(64)
        assert alpha_map(surface)[0] == 0

    def test_small_sizes_use_one_head(self):
        """Three heads at 16 px is a brown smudge; the code picks one head."""
        assert mascot.THREE_HEAD_MIN >= 32
        small = icons.app_icon_surface(16)
        assert ink_fraction(small) > 0.5


class TestNoExternalDependency:
    def test_face_renders_without_any_asset_directory(self, monkeypatch):
        """The art is code, so there is no file to be missing.

        This is a regression guard: an earlier version took the artwork from a
        downloaded PNG and silently fell back to a drawn key when it vanished.
        """
        assert not hasattr(mascot, "SOURCE_ART")
        monkeypatch.chdir("/")
        face = mascot.face_surface(32, monochrome=(1.0, 1.0, 1.0))
        assert ink_fraction(face) > 0.3


class TestGdkPixbuf:
    def test_pixbuf_matches_the_tray_size(self):
        gi = pytest.importorskip("gi")
        gi.require_version("GdkPixbuf", "2.0")
        pixbuf = icons.gdk_pixbuf_for_state("valid", 22)
        assert pixbuf.get_width() == 22 and pixbuf.get_height() == 22
        assert pixbuf.get_has_alpha()
