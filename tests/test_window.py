"""GUI smoke tests for the account editor.

Every bug this file guards against was found by building the real dialog, not by
reading the code, and each one was invisible to compile, lint and the rest of the
suite:

* ``Adw.EntryRow`` has no ``set_placeholder_text``, so editing a saved password
  account raised ``AttributeError`` the moment the method row was touched.
* A nested ``if``/``else`` used to set that text made the failure look like a keyring
  problem rather than a missing property.

They are skipped when there is no display, because creating a GTK window without
one aborts the process rather than raising. That keeps the suite runnable over
SSH and in CI while still catching the failures on a desktop.
"""

from __future__ import annotations

import os

import pytest

if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
    pytest.skip("no display", allow_module_level=True)

gi = pytest.importorskip("gi")

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from krbctl.config import (  # noqa: E402
    METHOD_KEYTAB,
    METHOD_PASSWORD,
    METHOD_SESSION,
    Account,
)
from krbctl.window import EditorWindow  # noqa: E402


@pytest.fixture
def editor():
    """An EditorWindow with the given account, torn down afterwards."""
    Adw.init()
    parent = Gtk.Window()

    def build(account=None):
        return EditorWindow(parent, account)

    yield build
    parent.destroy()


def test_every_method_index_maps_to_its_value(editor):
    window = editor()
    assert len(EditorWindow._METHOD_ORDER) == window.method_row.get_model().get_n_items()

    for index, expected in enumerate(EditorWindow._METHOD_ORDER):
        window.method_row.set_selected(index)
        assert window._method() == expected


def test_selection_survives_an_out_of_range_index(editor):
    """Falls back rather than raising for a value the combo cannot show."""
    window = editor()
    window.method_row.set_selected(Gtk.INVALID_LIST_POSITION)
    assert window._method() == METHOD_KEYTAB


def test_session_account_hides_the_credential_rows(editor):
    """A session ticket has no password and no keytab to offer."""
    window = editor(Account(id="a", principal="me@REALM", method=METHOD_SESSION))
    assert window.method_row.get_selected() == EditorWindow._METHOD_ORDER.index(METHOD_SESSION)
    assert window.password_row.get_visible() is False
    assert window.remember_row.get_visible() is False
    assert window.keytab_row.get_visible() is False
    assert window.make_keytab_row.get_visible() is False


def test_only_the_relevant_rows_show_per_method(editor):
    window = editor()
    expected = {
        METHOD_KEYTAB: (True, False, False),
        METHOD_PASSWORD: (False, True, True),
        METHOD_SESSION: (False, False, False),
    }
    for index, method in enumerate(EditorWindow._METHOD_ORDER):
        window.method_row.set_selected(index)
        keytab, password, keyring = expected[method]
        assert window.keytab_row.get_visible() is keytab, method
        assert window.password_row.get_visible() is password, method
        assert window.remember_row.get_visible() is keyring, method


def test_editing_a_password_account_does_not_crash(editor):
    """Regression: this raised AttributeError on set_placeholder_text."""
    window = editor(Account(id="a", principal="me@REALM", method=METHOD_PASSWORD))
    for index in range(len(EditorWindow._METHOD_ORDER)):
        window.method_row.set_selected(index)
    window.method_row.set_selected(EditorWindow._METHOD_ORDER.index(METHOD_PASSWORD))


def test_saving_a_session_account_keeps_its_method(editor):
    saved = []
    window = editor(Account(id="a", principal="me@REALM", method=METHOD_SESSION))
    window._on_save = saved.append
    window._on_save_clicked(window._save_button)

    assert saved, "save produced no account"
    assert saved[0].method == METHOD_SESSION
    # No keytab path and no cache: both are the session's business, not the app's.
    assert saved[0].keytab == ""
    assert saved[0].cache == ""


def test_switching_from_keytab_to_session_drops_the_keytab_path(editor):
    """Otherwise a session account would keep pointing at a keytab it never uses."""
    saved = []
    window = editor()
    window.principal_row.set_text("me@REALM")
    window.method_row.set_selected(EditorWindow._METHOD_ORDER.index(METHOD_SESSION))
    window._on_save = saved.append
    window._on_save_clicked(window._save_button)
    assert saved and saved[0].keytab == ""


def test_a_blank_principal_is_not_saved(editor):
    """One keyring write is attributed to an explicit save, so a no-op must not fire one."""
    saved = []
    window = editor()
    window._on_save = saved.append
    window._on_save_clicked(window._save_button)
    assert saved == []
