"""Icon tests — the artwork pipeline, not the aesthetics.

The bugs worth guarding against here are the silent kind: a mask that fills a
solid rectangle instead of the silhouette, a wrong alpha byte offset, or a
missing file that crashes the tray at login instead of falling back.
"""

from __future__ import annotations

import pytest

from krbctl import icons


@pytest.fixture(scope="module")
def art():
    surface = icons.source_art()
    if surface is None:
        pytest.skip("Cerberus artwork not present")
    return surface


class TestArtwork:
    def test_artwork_has_a_real_alpha_channel(self, art):
        """The raw download has no alpha (the checkerboard is painted in).

        If prepare-art.py is ever skipped, the mask becomes a solid block and
        the tray shows a filled square. This is the canary for that.
        """
        data = bytes(art.get_data())
        alphas = data[3::4]  # ARGB32 little-endian: alpha is byte 3
        transparent = sum(1 for value in alphas if value == 0)
        inked = sum(1 for value in alphas if value == 255)
        assert transparent > len(alphas) * 0.3, "background was not keyed out"
        assert inked > len(alphas) * 0.2, "silhouette missing"

    def test_artwork_is_cropped_tight(self, art):
        width, height = art.get_width(), art.get_height()
        assert 300 < width < 1200 and 300 < height < 1200
        # The three-headed emblem is wider than it is tall.
        assert 1.3 < width / height < 1.8


class TestStateSurfaces:
    @pytest.mark.parametrize("state", ["valid", "expiring", "expired", "none", "error"])
    def test_shape_is_not_a_solid_block(self, state):
        """A keyed mask leaves most of a 22 px square empty."""
        surface = icons.surface_for_state(state, 22)
        data = bytes(surface.get_data())
        alphas = data[3::4]
        painted = sum(1 for value in alphas if value > 0)
        assert painted > 40, f"{state}: barely anything drawn"
        assert painted < len(alphas) * 0.75, f"{state}: looks like a filled square"

    @pytest.mark.parametrize("state", ["valid", "expiring", "expired", "none", "error"])
    def test_distinct_colours_per_state(self, state):
        """Each state is tinted with its own palette colour.

        Cairo stores premultiplied ARGB, and the faded "none" state never
        reaches alpha 255, so the ink colour has to be divided back out by the
        pixel's own alpha before comparing.
        """
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
                    # un-premultiply: stored channel = colour * alpha / 255
                    found = tuple(round(data[index + c] * 255 / alpha) for c in (2, 1, 0))
                    break
            if found:
                break
        assert found is not None, f"{state}: nothing drawn"

        expected = icons.STATE_COLORS[state]
        expected_rgb = tuple(int(round(channel * 255)) for channel in expected)
        for got, want in zip(found, expected_rgb):
            assert abs(got - want) <= 3, f"{state}: {found} != {expected_rgb}"

    def test_states_are_visually_distinct(self):
        """No two states may share a colour, or the icon conveys nothing."""
        seen = {}
        for state, colour in icons.STATE_COLORS.items():
            assert colour not in seen, f"{state} duplicates {seen[colour]}"
            seen[colour] = state
        assert len(seen) == 5

    def test_none_state_is_faded(self):
        """'No ticket' must look inactive, not like a valid green ticket."""
        surface = icons.surface_for_state("none", 22)
        alphas = bytes(surface.get_data())[3::4]
        assert max(alphas) < 255, "the 'none' state should be semi-transparent"

    def test_sizes_are_square_and_requested(self):
        for size in (16, 22, 24, 48):
            surface = icons.surface_for_state("valid", size)
            assert (surface.get_width(), surface.get_height()) == (size, size)


class TestTrayPixmap:
    def test_signature_matches_sni(self):
        pixmap = icons.sni_pixmap("valid", 22)
        assert isinstance(pixmap, list) and len(pixmap) == 1
        width, height, data = pixmap[0]
        assert (width, height) == (22, 22)
        assert isinstance(data, bytes)

    def test_buffer_is_argb_big_endian(self):
        """SNI wants [A, R, G, B] per pixel; cairo gives [B, G, R, A]."""
        width, height, data = icons.sni_pixmap("valid", 22)[0]
        assert len(data) == width * height * 4
        # A tinted icon must have some fully opaque pixels in the A slot.
        assert max(data[0::4]) == 255
        # And the green channel should dominate for a green state: green is
        # byte 2 in big-endian ARGB.
        inked = [
            (data[i], data[i + 1], data[i + 2])
            for i in range(0, len(data), 4)
            if data[i] == 255
        ]
        assert inked, "no fully opaque pixels"
        _, red, green = inked[0]
        assert green > red, "green state is not green"


class TestAppIcon:
    def test_renders_at_each_size(self):
        for size in (16, 48, 256):
            surface = icons.app_icon_surface(size)
            assert (surface.get_width(), surface.get_height()) == (size, size)

    def test_has_an_opaque_background(self):
        """The launcher icon needs a slab, or black-on-dark disappears."""
        surface = icons.app_icon_surface(64)
        data = bytes(surface.get_data())
        alphas = data[3::4]
        opaque = sum(1 for value in alphas if value > 250)
        # Rounded corners leave the corners clear, so not the whole square.
        assert opaque > len(alphas) * 0.85
        assert opaque < len(alphas), "corners should be rounded, not square"

    def test_corner_is_transparent(self):
        surface = icons.app_icon_surface(64)
        stride = surface.get_stride()
        data = bytes(surface.get_data())
        assert data[3] == 0, "top-left corner should be outside the rounded rect"


class TestFallback:
    def test_missing_artwork_falls_back_to_the_drawn_key(self, monkeypatch):
        """A missing file must not leave a blank or a crash at login."""
        monkeypatch.setattr(icons, "_region", lambda region: None)
        surface = icons.surface_for_state("valid", 22)
        alphas = bytes(surface.get_data())[3::4]
        assert sum(1 for value in alphas if value > 0) > 40
