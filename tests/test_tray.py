"""The tray advertises the drawn plate, not a themed icon name.

A StatusNotifierItem host that finds IconName set draws *that* and never looks
at IconPixmap. So a name here silently replaces the state plate -- and the state
colour is the entire point of the tray icon. On Noctalia the name
"dialog-password-symbolic" rendered as a keyhole and the plate was never seen.
"""

import pytest

gi = pytest.importorskip("gi")

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from krbctl import icons  # noqa: E402
from krbctl.tray import Tray  # noqa: E402


@pytest.fixture
def tray():
    return Tray(app_id="krbctl-icon-test")


def value_of(item, prop):
    """Unwrapped value of a SNI property."""
    return item._prop_value(prop).unpack()


class TestIconName:
    def test_icon_name_is_empty(self, tray):
        assert value_of(tray, "IconName") == ""

    def test_no_themed_name_is_ever_advertised(self, tray):
        """A name would take precedence over the pixmap in a conforming host."""
        for state in ("valid", "expiring", "expired", "error", "none"):
            tray.state = state
            assert value_of(tray, "IconName") == "", state

    def test_overlay_and_attention_names_are_also_empty(self, tray):
        assert value_of(tray, "OverlayIconName") == ""
        assert value_of(tray, "AttentionIconName") == ""


class TestIconPixmap:
    def test_a_pixmap_is_always_present(self, tray):
        for state in ("valid", "expiring", "expired", "error", "none"):
            tray.state = state
            pixmap = value_of(tray, "IconPixmap")
            assert len(pixmap) == 1, state
            width, height, data = pixmap[0]
            assert width > 0 and height > 0, state
            assert width * height * 4 == len(data), state

    def test_pixmap_matches_the_state_colour(self, tray):
        """The colour is the message: swapping states must change the bytes."""
        def plate(state):
            tray.state = state
            return bytes(value_of(tray, "IconPixmap")[0][2])

        assert plate("valid") != plate("expired")
        assert plate("valid") == plate("valid")

    def test_each_state_matches_icons_module(self, tray):
        for state in ("valid", "expiring", "expired", "error", "none"):
            tray.state = state
            # `ay` unpacks to a list of ints; compare on bytes.
            actual = value_of(tray, "IconPixmap")[0]
            expected = icons.sni_pixmap(state, 22)[0]
            assert (actual[0], actual[1], bytes(actual[2])) == (
                expected[0],
                expected[1],
                bytes(expected[2]),
            ), state

    def test_tooltip_carries_the_state_plate(self, tray):
        """A host showing only the tooltip still gets the colour."""
        tray.state = "valid"
        _icon_name, pixmap, title, _description = value_of(tray, "ToolTip")
        assert pixmap, "tooltip must carry a pixmap"
        assert title == tray.title


class TestStateChangesAreAnnounced:
    def test_set_state_updates_the_icon(self, tray):
        tray.state = "valid"
        first = bytes(value_of(tray, "IconPixmap")[0][2])
        tray.set_state("expired", "expired")
        assert bytes(value_of(tray, "IconPixmap")[0][2]) != first

    def test_set_state_keeps_icon_name_empty(self, tray):
        tray.set_state("expiring", "expiring soon")
        assert value_of(tray, "IconName") == ""


class TestVariantShapes:
    """PyGObject rejects a malformed variant at call time, not at build time."""

    def test_every_property_builds(self, tray):
        for prop in (
            "Category",
            "Id",
            "Title",
            "Status",
            "IconName",
            "IconPixmap",
            "OverlayIconName",
            "AttentionIconName",
            "AttentionIconPixmap",
            "ToolTip",
            "ItemIsMenu",
            "Menu",
        ):
            built = tray._prop_value(prop)
            assert isinstance(built, GLib.Variant), prop

    def test_unknown_property_is_an_empty_string(self, tray):
        assert tray._prop_value("NoSuchProperty").unpack() == ""
