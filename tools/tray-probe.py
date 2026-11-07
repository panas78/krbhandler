"""Standalone probe for the tray: run it and inspect D-Bus from another shell.

    python3 tools/tray-probe.py

It registers a fake SNI item, prints whatever the watcher says, and quits after
10 seconds. Useful when an icon does not appear and the reason could be either
half of the protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from krbctl.tray import MenuItem, Tray  # noqa: E402


def main() -> int:
    app = Adw.Application(application_id="io.github.pan.Krbctl.Probe")

    def on_activate(_app):
        # A GApplication with no window exits as soon as activate returns, so
        # hold it open for the duration of the probe.
        _app.hold()
        tray = Tray(
            app_id="probe",
            title="Krbctl probe",
            on_activate=lambda: print("ACTIVATE received", flush=True),
        )
        tray.start()
        tray.set_state("valid", "9h42m — probe ticket")

        items = [
            MenuItem(id=1, label="Probe item", enabled=True),
            MenuItem(id=2, kind="separator"),
            MenuItem(id=3, label="Quit", action="quit"),
        ]
        root = MenuItem(id=0, kind="submenu", children=items)
        tray.set_menu([root], {"quit": _app.quit})

        def report():
            print(f"bus name      : {tray._bus_name}", flush=True)
            print(f"conn          : {tray._conn is not None}", flush=True)
            print(f"registered    : sni={tray._sni_ids} menu={tray._menu_ids}", flush=True)
            print(f"watcher known : {tray.available}", flush=True)
            return False

        GLib.timeout_add_seconds(2, report)
        GLib.timeout_add_seconds(10, _app.quit)

    app.connect("activate", on_activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
