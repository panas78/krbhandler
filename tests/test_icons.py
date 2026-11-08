"""Icon tests — the tray indicator and the shipped icon set.

Two renderings are tested separately, because they answer different questions:

* **The tray** must show the app icon, so it is identifiable in a bar, and must
  add the state colour only when the state needs acting on.
* **The window** keeps the solid state plate, which reads faster than a picture
  when it sits next to an account name.

The failures worth guarding against are the silent ones:

* a tray icon that renders as nothing, so the bar shows a gap;
* the app icon silently not loading, so the tray falls back without anyone
  noticing the set went missing;
* a wrong byte order, so a valid green ticket looks red;
* the state being lost entirely, so a broken ticket looks like a healthy one;
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


@pytest.fixture(autouse=True)
def isolated_icon(monkeypatch):
    """Pin the tray icon to the repo's own 22 px PNG.

    Without this the tests would pass or fail depending on whether krbctl
    happens to be installed on the machine running them.
    """
    monkeypatch.setenv("KRBCTL_ICON_PATH", str(HICOLOR / "22x22" / "apps" / "krbctl.png"))
    icons.clear_icon_cache()
    yield
    icons.clear_icon_cache()


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
        _, _, data = icons.sni_pixmap("valid", 22)[0]
        assert max(data[0::4]) == 255, "no opaque alpha byte"

    def test_rows_are_tightly_packed(self):
        """Odd widths would leave cairo's stride padding in the buffer.

        A 22 px surface is padded to 24 px per row, and shipping that padding
        shifts every row of the tray icon sideways.
        """
        size = 22
        _, _, data = icons.sni_pixmap("valid", size)[0]
        assert len(data) == size * size * 4
        surface = icons.tray_surface_for_state("valid", size)
        assert surface.get_stride() >= size * 4

    def test_every_state_produces_a_full_buffer(self):
        for state in icons.STATE_COLORS:
            width, height, data = icons.sni_pixmap(state, 22)[0]
            assert len(data) == width * height * 4
            assert any(data[0::4]), f"{state}: fully transparent pixmap"


class TestTrayShowsTheAppIcon:
    """The tray is the app's face in the bar, so it must be the app's icon."""

    def test_the_icon_file_is_the_one_that_was_installed(self):
        assert icons.app_icon_path(22) == HICOLOR / "22x22" / "apps" / "krbctl.png"

    def test_the_nearest_committed_size_is_preferred(self, monkeypatch, tmp_path):
        """A bar asking for 24 px should get the 24 px PNG, not a resize."""
        monkeypatch.delenv("KRBCTL_ICON_PATH")
        data_home = tmp_path / "share"
        for size in (16, 22, 24, 48):
            target = data_home / "icons" / "hicolor" / f"{size}x{size}" / "apps"
            target.mkdir(parents=True)
            (target / "krbctl.png").write_bytes(
                (HICOLOR / f"{size}x{size}" / "apps" / "krbctl.png").read_bytes()
            )
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        icons.clear_icon_cache()

        assert "24x24" in str(icons.app_icon_path(24))
        assert "16x16" in str(icons.app_icon_path(16))

    def test_a_healthy_tray_icon_is_the_untouched_artwork(self):
        """No dot on "valid": a working ticket announces nothing."""
        rendered = icons.tray_surface_for_state("valid", 22)
        source = cairo.ImageSurface.create_from_png(str(icons.app_icon_path(22)))
        assert bytes(rendered.get_data()) == bytes(source.get_data())

    def test_the_artwork_survives_in_the_top_left(self):
        """A dot in the corner must not repaint the whole icon."""
        rendered = icons.tray_surface_for_state("expired", 22)
        source = cairo.ImageSurface.create_from_png(str(icons.app_icon_path(22)))
        assert pixel_at(rendered, 3, 3) == pixel_at(source, 3, 3)


class TestStateDot:
    """The state is still on the tray, just quiet when nothing is wrong."""

    @pytest.mark.parametrize("state", icons.BADGED_STATES)
    def test_troubled_states_get_a_dot(self, state):
        plain = icons.tray_surface_for_state("valid", 22)
        troubled = icons.tray_surface_for_state(state, 22)
        assert bytes(plain.get_data()) != bytes(troubled.get_data()), state

    @pytest.mark.parametrize("state", icons.BADGED_STATES)
    def test_the_dot_is_in_the_state_colour(self, state):
        """Sample the dot's centre and check its hue against STATE_COLORS."""
        rendered = icons.tray_surface_for_state(state, 22)
        size = 22
        outer = size * icons.DOT_OUTER
        centre = int(size - outer - size * 0.05)
        r, g, b, a = pixel_at(rendered, centre, centre)
        expected = [round(channel * 255) for channel in icons.STATE_COLORS[state]]
        assert a == 255, f"{state}: dot centre is transparent"
        assert (r, g, b) == pytest.approx(tuple(expected), abs=8), state

    def test_valid_has_no_dot_in_the_corner(self):
        """The corner must be untouched artwork, not merely a different colour."""
        rendered = icons.tray_surface_for_state("valid", 22)
        source = cairo.ImageSurface.create_from_png(str(icons.app_icon_path(22)))
        size = 22
        outer = size * icons.DOT_OUTER
        centre = int(size - outer - size * 0.05)
        # Where a troubled state paints the dot, a healthy one still shows the
        # artwork pixel for pixel.
        assert pixel_at(rendered, centre, centre) == pixel_at(source, centre, centre)

    def test_states_are_distinguishable_from_each_other(self):
        rendered = {
            state: bytes(icons.tray_surface_for_state(state, 22).get_data())
            for state in icons.STATE_COLORS
        }
        assert len(set(rendered.values())) == len(rendered)


class TestMissingIconFallsBack:
    """A broken icon set must degrade to the old plate, not to a blank square."""

    def test_absent_icon_falls_back_to_the_plate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KRBCTL_ICON_PATH", str(tmp_path / "nope.png"))
        icons.clear_icon_cache()
        assert icons.app_icon_path(22) is None

        fallback = icons.tray_surface_for_state("valid", 22)
        plate = icons.surface_for_state("valid", 22)
        assert bytes(fallback.get_data()) == bytes(plate.get_data())

    def test_fallback_still_shows_the_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KRBCTL_ICON_PATH", str(tmp_path / "nope.png"))
        icons.clear_icon_cache()
        valid = bytes(icons.tray_surface_for_state("valid", 22).get_data())
        expired = bytes(icons.tray_surface_for_state("expired", 22).get_data())
        assert valid != expired

    def test_a_corrupt_png_also_falls_back(self, monkeypatch, tmp_path):
        broken = tmp_path / "krbctl.png"
        broken.write_bytes(b"not a png")
        monkeypatch.setenv("KRBCTL_ICON_PATH", str(broken))
        icons.clear_icon_cache()

        rendered = icons.tray_surface_for_state("valid", 22)
        plate = icons.surface_for_state("valid", 22)
        assert bytes(rendered.get_data()) == bytes(plate.get_data())


class TestWindowKeepsThePlate:
    """Row badges in the window stay solid colour: they sit next to a name."""

    def test_gdk_pixbuf_is_still_the_plate(self):
        pixbuf = icons.gdk_pixbuf_for_state("valid", 20)
        assert (pixbuf.get_width(), pixbuf.get_height()) == (20, 20)
        rgba = bytes(pixbuf.get_pixels())
        row = pixbuf.get_rowstride()
        # Centre pixel of a plate is the state green, not artwork.
        index = 10 * row + 10 * 4
        r, g, b = rgba[index], rgba[index + 1], rgba[index + 2]
        expected = [round(channel * 255) for channel in icons.STATE_COLORS["valid"]]
        assert (r, g, b) == pytest.approx(tuple(expected), abs=8)


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
