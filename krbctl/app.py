"""Application glue: poller, tray wiring, auto-renew, notifications.

One loop drives everything. Every ``prefs.poll_seconds`` it reads the cached
ticket state (a few milliseconds of ``klist``), updates the tray icon and the
window, and — if the account says so — renews before the ticket runs out.

Renewal is deliberately conservative: it only fires while the ticket is still
valid, and a failed attempt backs off instead of hammering a KDC that is
unreachable because the VPN is down.
"""

from __future__ import annotations

import sys
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config, kerberos  # noqa: E402
from .config import METHOD_KEYTAB, Account  # noqa: E402
from .tray import MenuItem, Tray  # noqa: E402
from .window import MainWindow, run_async  # noqa: E402

ICON_NAME = "krbctl"

# Menu action ids, kept short — they round-trip through D-Bus labels.
ACT_RENEW = "renew"
ACT_DESTROY = "destroy"
ACT_DETAILS = "details"
ACT_WINDOW = "window"
ACT_QUIT = "quit"

# Don't retry a failing renewal more often than this (seconds).
RETRY_BACKOFF = 300


class KrbApplication(Adw.Application):
    def __init__(self, *, tray: bool = True, show_window: bool = False):
        super().__init__(
            application_id="io.github.pan.Krbctl",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.store = config.Store().load()
        self.tray: Optional[Tray] = None
        self.window: Optional[MainWindow] = None
        self._want_tray = tray
        self._want_window = show_window
        self._statuses: dict[str, kerberos.Status] = {}
        self._last_notified: dict[str, str] = {}
        self._last_attempt: dict[str, float] = {}
        self._poll_source: Optional[int] = None
        self._busy = False
        self._actions = {
            ACT_RENEW: self._menu_renew,
            ACT_DESTROY: self._menu_destroy,
            ACT_DETAILS: self._menu_details,
            ACT_WINDOW: self.show_window,
            ACT_QUIT: self.quit,
        }

    # ── startup ───────────────────────────────────────────────

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # There is no window at startup yet, so ask GDK for the default display
        # rather than climbing through a window that does not exist.
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(self._icon_dir())

    def _icon_dir(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent.parent / "data" / "icons")

    def do_command_line(self, command_line) -> int:
        args = command_line.get_arguments()[1:]
        if "--window" in args or not self._want_tray:
            self._want_window = True
        self.activate()
        return 0

    def do_activate(self) -> None:
        if self._want_tray and self.tray is None:
            self._start_tray()

        if self._want_window:
            self.show_window()
        else:
            # Tray-only mode has no window, so hold the application open.
            self.hold()

        self._schedule_poll(immediate=True)

    def _start_tray(self) -> None:
        self.tray = Tray(
            app_id=self.get_application_id() or "krbctl",
            title="Kerberos Tickets",
            on_activate=self.show_window,
            on_secondary=self._menu_details,
        )
        self.tray.start()
        self._refresh_menu()

    # ── window ────────────────────────────────────────────────

    def show_window(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, self.store)
            self.window.set_icon_name(ICON_NAME)
            self.window.connect("close-request", self._on_window_closed)
        self.window.set_statuses(self._statuses)
        self.window.present()

    def _on_window_closed(self, _window) -> bool:
        # With a tray the window just hides; quitting is an explicit menu item.
        if self.tray is not None and self.tray.available and self.window is not None:
            self.window.set_visible(False)
            return True
        return False

    # ── polling ───────────────────────────────────────────────

    def _schedule_poll(self, *, immediate: bool = False) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None
        if immediate:
            GLib.idle_add(self._poll)
        interval = max(5, int(self.store.prefs.poll_seconds))
        self._poll_source = GLib.timeout_add_seconds(interval, self._poll)

    def _poll(self) -> bool:
        if self._busy:
            return True
        self._busy = True

        def work():
            return {
                account.id: kerberos.status(
                    account.cache or None, warn_minutes=account.warn_minutes
                )
                for account in self.store.accounts
            }

        def ok(statuses: dict) -> None:
            self._busy = False
            previous = self._statuses
            self._statuses = statuses
            self._apply(statuses, previous)
            return False

        def fail(exc: Exception) -> None:
            self._busy = False
            print(f"krbctl: poll failed: {exc}", file=sys.stderr, flush=True)
            return False

        run_async(work, ok, fail)
        return True

    def _apply(self, statuses: dict, previous: dict) -> None:
        if self.window is not None:
            self.window.set_statuses(statuses)
        self._update_tray(statuses)
        self._maybe_auto_renew(statuses)
        self._maybe_notify(statuses, previous)

    def _worst(self, statuses: dict) -> str:
        order = ["valid", "expiring", "none", "expired", "error"]
        worst = "valid"
        for status in statuses.values():
            if order.index(status.state) > order.index(worst):
                worst = status.state
        return worst if statuses else "none"

    def _summary(self, statuses: dict) -> str:
        if not statuses:
            return "No account configured"
        parts = []
        for account in self.store.accounts:
            status = statuses.get(account.id)
            if status is not None:
                parts.append(f"{account.label}: {status.label}")
        return " · ".join(parts)

    # ── tray ──────────────────────────────────────────────────

    def _update_tray(self, statuses: dict) -> None:
        if self.tray is None:
            return
        state = self._worst(statuses)
        self.tray.set_state(state, self._summary(statuses))
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        if self.tray is None:
            return

        items: list[MenuItem] = []
        next_id = [1]

        def new_id() -> int:
            value = next_id[0]
            next_id[0] += 1
            return value

        if not self.store.accounts:
            items.append(MenuItem(id=new_id(), label="No account configured", enabled=False))
        for account in self.store.accounts:
            status = self._statuses.get(account.id)
            badge = status.label if status else "…"
            state = status.state if status else "none"

            submenu_items = []
            if state in ("valid", "expiring", "expired", "none"):
                submenu_items.append(
                    MenuItem(id=new_id(), label="Renew now", action=ACT_RENEW)
                )
            if state in ("valid", "expiring"):
                submenu_items.append(
                    MenuItem(id=new_id(), label="Destroy ticket", action=ACT_DESTROY)
                )
            submenu_items.append(MenuItem(id=new_id(), label="Details…", action=ACT_DETAILS))

            items.append(
                MenuItem(
                    id=new_id(),
                    label=f"{badge}  {account.label}",
                    kind="submenu",
                    children=submenu_items,
                )
            )

        items.append(MenuItem(id=new_id(), kind="separator"))
        items.append(MenuItem(id=new_id(), label="Open Kerberos Tickets", action=ACT_WINDOW))
        items.append(MenuItem(id=new_id(), label="Quit", action=ACT_QUIT))

        root_children = items
        root = MenuItem(id=0, kind="submenu", children=root_children)
        self.tray.set_menu([root], self._actions)

    def _open_window(self) -> None:
        self.show_window()

    # ── menu actions ──────────────────────────────────────────

    def _first_account(self) -> Optional[Account]:
        return self.store.accounts[0] if self.store.accounts else None

    def _menu_renew(self) -> None:
        account = self._first_account()
        if account is None:
            return
        self._open_window()
        if self.window is not None:
            self.window.renew(account)

    def _menu_destroy(self) -> None:
        account = self._first_account()
        if account is None:
            return
        if self.window is not None:
            self.window.destroy_ticket(account)
        self._schedule_poll(immediate=True)

    def _menu_details(self) -> None:
        account = self._first_account()
        if account is None:
            self._open_window()
            return
        self._open_window()
        if self.window is not None:
            from .window import _ticket_lines

            self.window._show_text(f"klist — {account.principal}", _ticket_lines(account))

    # ── auto renew + notifications ────────────────────────────

    def _maybe_auto_renew(self, statuses: dict) -> None:
        now = GLib.get_monotonic_time() / 1_000_000
        for account in self.store.accounts:
            if not (account.auto_renew and self.store.prefs.auto_renew_global):
                continue
            status = statuses.get(account.id)
            if status is None or status.state not in ("expiring",):
                continue
            if account.method != METHOD_KEYTAB:
                continue  # never silently spend a stored password
            if now - self._last_attempt.get(account.id, 0) < RETRY_BACKOFF:
                continue
            self._last_attempt[account.id] = now

            def work(account=account):
                kerberos.kinit_from_keytab(account.principal, account.keytab, account.cache or None)
                return kerberos.status(account.cache or None, warn_minutes=account.warn_minutes)

            def ok(new_status, account=account):
                self._statuses[account.id] = new_status
                self._update_tray(self._statuses)
                if self.window is not None:
                    self.window.set_statuses(self._statuses)
                self._send_notification(
                    f"{account.label}: ticket renewed",
                    f"Valid for {new_status.label}",
                )
                return False

            def fail(exc, account=account):
                message = getattr(exc, "friendly", str(exc))
                print(f"krbctl: auto-renew failed for {account.principal}: {message}", flush=True)
                self._send_notification(
                    "Kerberos renewal failed",
                    f"{account.label}: {message}",
                )
                return False

            run_async(work, ok, fail)

    def _maybe_notify(self, statuses: dict, previous: dict) -> None:
        if not self.store.prefs.notify:
            return
        for account in self.store.accounts:
            status = statuses.get(account.id)
            if status is None:
                continue
            before = previous.get(account.id)
            before_state = before.state if before else None
            if status.state == before_state:
                continue
            # Only announce a worsening state, never a recovery — coming back
            # from expired to valid is expected and needs no interruption.
            if status.state in ("expiring", "expired") and before_state != status.state:
                body = status.detail or status.label
                self._send_notification(f"{account.label}: {status.state}", body)
            elif status.state == "error" and before_state is not None:
                self._send_notification(f"{account.label}: cannot read ticket", status.message)

    def _send_notification(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new(ICON_NAME))
        self.send_notification(None, notification)


def main(*, tray: bool = True, show_window: bool = False) -> int:
    app = KrbApplication(tray=tray, show_window=show_window)
    return app.run([sys.argv[0]])
