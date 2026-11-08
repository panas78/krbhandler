"""The GTK4 / libadwaita window.

Everything that talks to the KDC (``klist``, ``kinit``, ``ktutil``) can block for
seconds when the VPN is down, so it never runs on the GTK main loop: callers get
:func:`run_async` and results come back on the main thread via ``GLib.idle_add``.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import autostart, config, domain, icons, kerberos, secrets  # noqa: E402
from .config import METHOD_KEYTAB, METHOD_PASSWORD, METHOD_SESSION, Account  # noqa: E402

CSS = """
.krb-badge { font-feature-settings: "tnum"; font-weight: 600; }
.krb-valid { color: #a3be8c; }
.krb-expiring { color: #ebcb8b; }
.krb-expired { color: #bf616a; }
.krb-none { color: #8fbcbb; }
.krb-error { color: #bf616a; }
.krb-detail { font-family: monospace; font-size: 11px; }
"""


def run_async(
    work: Callable[[], object],
    on_success: Callable[[object], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Run blocking work off the main loop, deliver the result on it."""

    def worker() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 — every failure becomes a toast
            GLib.idle_add(on_error, exc)
        else:
            GLib.idle_add(on_success, result)

    threading.Thread(target=worker, daemon=True).start()


def _ticket_lines(account: Account) -> str:
    """Full ``klist`` output for the details dialog."""
    import subprocess

    env = dict(os.environ, LC_ALL="C", LANG="C")
    if account.cache:
        env["KRB5CCNAME"] = kerberos.expand(account.cache) or ""
    argv = ["klist"] if not account.cache else ["klist", "-c", kerberos.expand(account.cache) or ""]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"klist failed: {exc}"
    return (proc.stdout + proc.stderr).strip() or "(no output)"


class EditorWindow(Adw.Window):
    """Add or edit one account.

    The form is plain: principal, how to authenticate, and renewal policy. When
    the method is "Keytab" and the file does not exist yet, the same dialog can
    build one from the account password with ``ktutil`` — that removes the
    ``ktutil`` incantation from the user's life, which was the whole point.
    """

    # Row order in the Method combo, and therefore the mapping to the stored
    # method value. One place, so the two cannot drift.
    _METHOD_ORDER = (METHOD_KEYTAB, METHOD_PASSWORD, METHOD_SESSION)

    def __init__(
        self,
        parent: Gtk.Window,
        account: Optional[Account] = None,
        on_save: Optional[Callable[[Account], None]] = None,
    ):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Edit account" if account else "Add account")
        self.set_default_size(520, 640)

        self._existing = account
        self._on_save = on_save

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self._save_button = Gtk.Button(label="Save")
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_button)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.set_content(toolbar)

        # ── identity ──
        identity = Adw.PreferencesGroup(title="Identity")
        page.add(identity)

        self.principal_row = Adw.EntryRow(title="Principal")
        self.principal_row.set_text(account.principal if account else "")
        init = kerberos.default_realm()
        self.principal_row.set_tooltip_text(
            f"user@REALM — this realm is {init}" if init else "user@REALM"
        )
        identity.add(self.principal_row)

        self.label_row = Adw.EntryRow(title="Display name (optional)")
        self.label_row.set_text(account.label if account and account.label != account.principal else "")
        identity.add(self.label_row)

        # ── authentication ──
        auth = Adw.PreferencesGroup(title="Authentication")
        page.add(auth)

        self.method_row = Adw.ComboRow(title="Method")
        self.method_row.set_model(
            Gtk.StringList.new(
                [
                    "Keytab — unattended, no password",
                    "Password — stored in the keyring",
                    "Login session — the ticket SSSD already issued",
                ]
            )
        )
        self.method_row.set_selected(self._METHOD_ORDER.index(account.method) if account else 0)
        self.method_row.connect("notify::selected", lambda *_: self._sync_method())
        auth.add(self.method_row)

        self.keytab_row = Adw.EntryRow(title="Keytab file")
        self.keytab_row.set_text(account.keytab if account else config.DEFAULT_KEYTAB)
        browse = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER)
        browse.add_css_class("flat")
        browse.connect("clicked", self._browse_keytab)
        self.keytab_row.add_suffix(browse)
        auth.add(self.keytab_row)

        self.password_row = Adw.PasswordEntryRow(title="Password")
        auth.add(self.password_row)

        self.remember_row = Adw.SwitchRow(
            title="Save password in the keyring",
            subtitle="Needed for automatic renewal without a prompt",
        )
        self.remember_row.set_active(account.remember_password if account else True)
        auth.add(self.remember_row)

        self.make_keytab_row = Adw.ActionRow(
            title="Create keytab from password",
            subtitle="Writes the keytab so future renewals need no password",
        )
        make_button = Gtk.Button(label="Create", valign=Gtk.Align.CENTER)
        make_button.connect("clicked", self._make_keytab)
        self.make_keytab_row.add_suffix(make_button)
        self.make_keytab_row.set_activatable_widget(make_button)
        auth.add(self.make_keytab_row)

        # ── policy ──
        policy = Adw.PreferencesGroup(title="Renewal")
        page.add(policy)

        self.auto_row = Adw.SwitchRow(
            title="Renew automatically",
            subtitle="Refresh the ticket when it is close to expiring",
        )
        self.auto_row.set_active(account.auto_renew if account else True)
        policy.add(self.auto_row)

        self.cache_row = Adw.EntryRow(title="Credential cache (optional)")
        self.cache_row.set_text(account.cache if account else "")
        self.cache_row.set_tooltip_text("Leave empty to use the session default cache")
        policy.add(self.cache_row)

        self.warn_row = Adw.SpinRow.new_with_range(1, 240, 1)
        self.warn_row.set_title("Warn before expiry (minutes)")
        self.warn_row.set_value(account.warn_minutes if account else config.DEFAULT_WARN_MINUTES)
        policy.add(self.warn_row)

        self._sync_method()

    # ── behaviour ─────────────────────────────────────────────

    def _method(self) -> str:
        index = self.method_row.get_selected()
        if 0 <= index < len(self._METHOD_ORDER):
            return self._METHOD_ORDER[index]
        return METHOD_KEYTAB

    def _uses_keytab(self) -> bool:
        return self._method() == METHOD_KEYTAB

    def _is_session(self) -> bool:
        return self._method() == METHOD_SESSION

    def _sync_method(self) -> None:
        keytab = self._uses_keytab()
        session = self._is_session()
        self.keytab_row.set_visible(keytab)
        self.make_keytab_row.set_visible(keytab)
        # A session account supplies no credentials of its own, so neither the
        # password field nor the keyring switch applies to it.
        self.password_row.set_visible(not keytab and not session)
        self.remember_row.set_visible(not keytab and not session)
        if not keytab and not session and self._existing:
            # Never re-ask for a password we already hold. Adw.EntryRow has no
            # placeholder property, so the hint goes on the row like the
            # principal and cache rows above.
            self.password_row.set_tooltip_text(
                "Stored in the keyring — leave empty to keep"
                if secrets.lookup_password(self._existing.principal)
                else "Needed to sign in"
            )

    def _browse_keytab(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Select keytab")
        keytab = Gio.File.new_for_path(kerberos.expand(self.keytab_row.get_text()) or os.path.expanduser("~"))

        def done(source, result, _data):
            try:
                chosen = source.open_finish(result)
            except GLib.Error:
                return
            path = chosen.get_path() or ""
            home = os.path.expanduser("~")
            self.keytab_row.set_text(path.replace(home, "~", 1) if path.startswith(home) else path)

        dialog.open(self, None, done, None)
        del keytab

    def _toast(self, message: str) -> None:
        # The editor has no toast overlay; the parent window shows it instead.
        parent = self.get_transient_for()
        if isinstance(parent, MainWindow):
            parent.toast(message)

    def _make_keytab(self, _button: Gtk.Button) -> None:
        principal = self.principal_row.get_text().strip()
        password = self.password_row.get_text()
        path = self.keytab_row.get_text().strip() or config.DEFAULT_KEYTAB
        if not principal:
            self._toast("Enter a principal first")
            return
        if not password:
            self.password_row.set_visible(True)
            self._toast("Type the password, then press Create")
            return

        self._save_button.set_sensitive(False)

        def work():
            return kerberos.create_keytab(principal, password, path)

        def ok(written):
            self._save_button.set_sensitive(True)
            self.password_row.set_text("")
            self._toast(f"Keytab written to {written}")

        def fail(exc):
            self._save_button.set_sensitive(True)
            self._toast(getattr(exc, "friendly", str(exc)))

        run_async(work, ok, fail)

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        principal = self.principal_row.get_text().strip()
        if not principal:
            self._toast("A principal is required")
            return
        if "@" not in principal:
            realm = kerberos.default_realm()
            if realm:
                principal = f"{principal}@{realm}"

        keytab = self._uses_keytab()
        account = Account(
            id=self._existing.id if self._existing else "",
            principal=principal,
            label=self.label_row.get_text().strip() or principal,
            method=self._method(),
            keytab=(self.keytab_row.get_text().strip() or config.DEFAULT_KEYTAB) if keytab else "",
            cache=self.cache_row.get_text().strip(),
            auto_renew=self.auto_row.get_active(),
            remember_password=self.remember_row.get_active(),
            warn_minutes=int(self.warn_row.get_value()),
        )

        # A typed password is stored (or used once) here, not on save, so the
        # keyring write is attributed to an explicit user action.
        password = self.password_row.get_text()
        if not keytab and not self._is_session() and password:
            if account.remember_password:
                if not secrets.store_password(principal, password):
                    self._toast("No keyring available — the password will be asked each time")
                    account.remember_password = False
            else:
                account.remember_password = False

        if self._on_save:
            self._on_save(account)
        self.close()


class DomainWindow(Adw.Window):
    """Join this machine to an Active Directory domain.

    Joining is the one thing this app does that changes the whole machine, so the
    dialog shows the readiness checks first: the common failures (packages
    missing, oddjobd off, a one-word hostname) are all visible before the user
    types a password, instead of surfacing as an obscure realmd error.
    """

    def __init__(self, parent: Gtk.Window, on_done: Optional[Callable[[], None]] = None):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Join a domain")
        self.set_default_size(560, 720)
        self._on_done = on_done
        self._busy = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self._join_button = Gtk.Button(label="Join")
        self._join_button.add_css_class("suggested-action")
        self._join_button.connect("clicked", self._on_join_clicked)
        header.pack_end(self._join_button)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        # The overlay must be the window's content and the toolbar its child —
        # setting the toolbar as content first gives it a parent, and
        # set_child() then fails its precondition.
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        # ── credentials ──
        creds = Adw.PreferencesGroup(
            title="Credentials",
            description="An account allowed to join computers to the domain",
        )
        page.add(creds)

        self.realm_row = Adw.EntryRow(title="Domain / realm")
        self.realm_row.set_text(domain.suggested_realm())
        creds.add(self.realm_row)

        self.user_row = Adw.EntryRow(title="Account")
        creds.add(self.user_row)

        self.password_row = Adw.PasswordEntryRow(title="Password")
        creds.add(self.password_row)
        # Enter in the password field should submit, like any login form.
        self.password_row.connect("entry-activated", lambda *_: self._on_join_clicked())

        self.ou_row = Adw.EntryRow(title="Computer OU (optional)")
        self.ou_row.set_tooltip_text("DN of the OU to create the computer account in")
        creds.add(self.ou_row)

        # ── readiness ──
        self.checks_group = Adw.PreferencesGroup(title="Readiness")
        page.add(self.checks_group)
        self._check_rows: list[Gtk.Widget] = []

        self.install_row = Adw.ActionRow(
            title="Install the domain packages",
            subtitle="realmd, sssd, adcli and the home-directory helper",
        )
        install_button = Gtk.Button(label="Install", valign=Gtk.Align.CENTER)
        install_button.connect("clicked", self._on_install_clicked)
        self.install_row.add_suffix(install_button)
        self.install_row.set_activatable_widget(install_button)
        self.install_row.set_visible(False)
        self.checks_group.add(self.install_row)

        self.refresh_checks()

    # ── readiness ─────────────────────────────────────────────

    def refresh_checks(self) -> None:
        for row in list(self._check_rows):
            self.checks_group.remove(row)
        self._check_rows.clear()

        def work():
            return domain.status()

        def ok(payload: dict) -> None:
            for check in payload["checks"]:
                row = Adw.ActionRow(title=check["name"], subtitle=check["detail"] or "—")
                if not check["ok"]:
                    row.add_prefix(
                        Gtk.Image.new_from_icon_name(
                            "dialog-warning-symbolic"
                            if check["severity"] == "warning"
                            else "dialog-error-symbolic"
                        )
                    )
                else:
                    row.add_prefix(
                        Gtk.Image.new_from_icon_name("object-select-symbolic")
                    )
                self.checks_group.add(row)
                self._check_rows.append(row)

            missing = domain.missing_packages()
            self.install_row.set_visible(bool(missing))
            # The install row must stay above the check rows.
            self.checks_group.set_description(
                "Missing packages must be installed before joining" if missing else ""
            )
            self._join_button.set_sensitive(not missing and not self._busy)

        def fail(exc: Exception) -> None:
            self.toast(str(exc))

        run_async(work, ok, fail)

    # ── actions ───────────────────────────────────────────────

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast.new(message))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._join_button.set_sensitive(not busy)
        self._join_button.set_label("Joining…" if busy else "Join")

    def _on_install_clicked(self, _button: Gtk.Button) -> None:
        self.toast("Installing packages — approve the administrator prompt")
        self._set_busy(True)

        def ok(_result) -> None:
            self._set_busy(False)
            self.toast("Packages installed")
            self.refresh_checks()

        def fail(exc: Exception) -> None:
            self._set_busy(False)
            self.toast(str(exc))

        run_async(domain.install_prerequisites, ok, fail)

    def _on_join_clicked(self, _button: Gtk.Button) -> None:
        if self._busy:
            return
        realm_name = self.realm_row.get_text().strip()
        user = self.user_row.get_text().strip()
        password = self.password_row.get_text()

        # Checked here rather than allowing realmd to fail later: a blank field
        # costs a polkit prompt and a confusing error.
        if not realm_name:
            self.toast("Enter the domain name")
            return
        if not user:
            self.toast("Enter the account to join with")
            return
        if not password:
            self.toast("Enter the password")
            return

        self._set_busy(True)

        def work():
            return domain.join(
                realm_name,
                user,
                password,
                computer_ou=self.ou_row.get_text().strip() or None,
                permit_all=True,
            )

        def ok(_payload) -> None:
            # Drop the password from the widget as soon as it is no longer needed.
            self.password_row.set_text("")
            self._set_busy(False)
            self.toast(f"Joined {realm_name}")
            if self._on_done is not None:
                self._on_done()
            self.close()

        def fail(exc: Exception) -> None:
            self.password_row.set_text("")
            self._set_busy(False)
            message = getattr(exc, "friendly", str(exc))
            self.toast(message)

        run_async(work, ok, fail)


class MainWindow(Adw.ApplicationWindow):
    """Account list, live ticket state, and the manual actions."""

    def __init__(self, app: Adw.Application, store: config.Store):
        super().__init__(application=app, title="Kerberos Tickets")
        self.set_default_size(560, 680)
        self.store = store
        self._statuses: dict[str, kerberos.Status] = {}
        self._groups: dict[str, Adw.PreferencesGroup] = {}
        self._empty_rows: list[Gtk.Widget] = []

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        header = Adw.HeaderBar()
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_tooltip_text("Add account")
        add_button.connect("clicked", lambda *_: self.open_editor(None))
        header.pack_start(add_button)

        menu = Gio.Menu()
        menu.append("Diagnose setup", "win.doctor")
        menu.append("About", "win.about")
        menu.append("Quit", "win.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        self._page = Adw.PreferencesPage()
        toolbar.set_content(self._page)

        self._empty_group = Adw.PreferencesGroup()
        self._page.add(self._empty_group)

        # Built once and re-appended last by rebuild(), so it stays pinned to the
        # bottom no matter how many accounts appear or disappear above it.
        self._syncing_startup = False
        self._startup_group = self._build_startup_group()
        self._domain_group = self._build_domain_group()

        self._install_actions()
        self._apply_css()
        self.rebuild()

    # ── chrome ────────────────────────────────────────────────

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        display = self.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _install_actions(self) -> None:
        for name, handler in (
            ("doctor", self._on_doctor),
            ("about", self._on_about),
            ("quit", lambda *_: self.get_application().quit()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast.new(message))

    def _on_about(self, *_args) -> None:
        from . import __version__

        about = Adw.AboutWindow(
            transient_for=self,
            application_name="Kerberos Tickets",
            application_icon="krbctl",
            version=__version__,
            developer_name="Panagiotis Perivolaris",
            comments="Manage Kerberos tickets from the tray or a window.",
        )
        about.present()

    def _on_doctor(self, *_args) -> None:
        from .cli import cmd_doctor
        import argparse
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            try:
                cmd_doctor(argparse.Namespace())
            except Exception as exc:  # noqa: BLE001
                buffer.write(f"doctor failed: {exc}")
        self._show_text("Setup diagnosis", buffer.getvalue())

    def _show_text(self, title: str, body: str) -> None:
        window = Adw.Window(transient_for=self, modal=True, title=title)
        window.set_default_size(560, 460)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: window.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)

        label = Gtk.Label(label=body, xalign=0, yalign=0)
        label.add_css_class("krb-detail")
        label.set_selectable(True)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        label.set_margin_start(16)
        label.set_margin_end(16)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(label)
        toolbar.set_content(scroller)
        window.set_content(toolbar)
        window.present()

    # ── content ───────────────────────────────────────────────

    def reload(self) -> None:
        self.store.load()
        self.rebuild()

    def _detach(self, widget: Gtk.Widget) -> None:
        """Remove a widget from the page only if it is actually there.

        adw_preferences_page_remove() logs a critical and keeps going when the
        child is not attached, which on the first rebuild means a burst of
        warnings for groups that were never added yet.
        """
        if widget.get_parent() is self._page:
            self._page.remove(widget)

    def rebuild(self) -> None:
        for group in list(self._groups.values()):
            self._detach(group)
        self._groups.clear()
        # AdwPreferencesGroup has no clear(); get_first_child() hands back its
        # internal box, not the rows, so the rows are tracked explicitly.
        for row in list(self._empty_rows):
            self._empty_group.remove(row)
        self._empty_rows.clear()
        self._detach(self._startup_group)
        self._detach(self._domain_group)

        if not self.store.accounts:
            self._empty_group.set_title("No accounts")
            row = Adw.ActionRow(
                title="Add your Kerberos account",
                subtitle="Use the + button, or drop a keytab in your home directory",
            )
            self._empty_group.add(row)
            self._empty_rows.append(row)
        else:
            self._empty_group.set_title("")
            for account in self.store.accounts:
                self._add_account_group(account)

        self._page.add(self._domain_group)
        self._page.add(self._startup_group)
        self._sync_startup_row()
        self._sync_domain_group()

    # ── domain ────────────────────────────────────────────────

    def _build_domain_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Domain")
        self._domain_row = Adw.ActionRow(title="Active Directory")
        self._domain_action = Gtk.Button(label="Join…", valign=Gtk.Align.CENTER)
        self._domain_action.connect("clicked", self._on_domain_action)
        self._domain_row.add_suffix(self._domain_action)
        self._domain_row.set_activatable_widget(self._domain_action)
        group.add(self._domain_row)
        return group

    def _sync_domain_group(self) -> None:
        """Show membership, or why joining is not possible yet.

        Read from disk every time: the CLI can join or leave while this window is
        open, and a stale "Join…" button on a joined machine invites a duplicate
        join.
        """
        details = domain.realm_details()
        if details:
            # Name the backend per realm: two memberships using different
            # clients (winbind and sssd against the same domain) is the
            # condition that breaks graphical logins, and "Joined to x, X"
            # alone hides it.
            labelled = ", ".join(
                f"{realm.name} ({realm.client_software})" if realm.client_software else realm.name
                for realm in details
            )
            self._domain_row.set_title(f"Joined to {labelled}")

            duplicates = domain.duplicate_memberships(details)
            policy = domain.login_policy().get("login-policy", "login policy unknown")
            if duplicates:
                self._domain_row.set_subtitle(
                    f"{policy} — same domain joined twice; leave once and rejoin"
                )
            else:
                self._domain_row.set_subtitle(policy)
            self._domain_action.set_label("Leave")
        else:
            self._domain_row.set_title("Not joined")
            missing = domain.missing_packages()
            self._domain_row.set_subtitle(
                "Packages missing: " + " ".join(missing)
                if missing
                else "Join to get Kerberos tickets from the domain"
            )
            self._domain_action.set_label("Join…")

    def _on_domain_action(self, _button: Gtk.Button) -> None:
        if domain.joined_realms():
            self._on_leave_domain()
            return
        DomainWindow(self, on_done=self.reload).present()

    def _on_leave_domain(self) -> None:
        realms = domain.joined_realms()
        if not realms:
            return
        realm_name = realms[0]

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Leave {realm_name}?",
            body="Domain accounts will no longer be able to sign in to this machine.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("leave", "Leave")
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response != "leave":
                return

            def ok(_r) -> None:
                self.toast(f"Left {realm_name}")
                self.reload()

            def fail(exc: Exception) -> None:
                self.toast(getattr(exc, "friendly", str(exc)))

            run_async(lambda: domain.leave(realm_name), ok, fail)

        dialog.connect("response", on_response)
        dialog.present()

    # ── startup ───────────────────────────────────────────────

    def _build_startup_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title("Startup")
        self._startup_row = Adw.SwitchRow(
            title="Start at login",
            subtitle="Show the ticket state in the bar without opening this window",
        )
        # notify::active rather than a click handler: the switch can be flipped
        # by keyboard, and by assistive tech that drives the property directly.
        self._startup_row.connect("notify::active", self._on_startup_toggled)
        group.add(self._startup_row)
        return group

    def _sync_startup_row(self) -> None:
        """Point the switch at what is actually on disk, not at what we hoped.

        Written state is the only truth here: another process (or the CLI) may
        have changed it since the window opened.
        """
        info = autostart.status()
        self._syncing_startup = True
        try:
            self._startup_row.set_active(info["enabled"])
            if not info["binary"]:
                self._startup_row.set_sensitive(False)
                self._startup_row.set_subtitle("krbctl is not installed — run ./install.sh")
            elif not info["binary_exists"]:
                self._startup_row.set_sensitive(False)
                self._startup_row.set_subtitle(f"Launcher is missing: {info['binary']}")
            else:
                self._startup_row.set_sensitive(True)
                self._startup_row.set_subtitle(
                    "Show the ticket state in the bar without opening this window"
                )
        finally:
            self._syncing_startup = False

    def _on_startup_toggled(self, row: Gtk.Widget, _pspec) -> None:
        if self._syncing_startup:
            return
        wanted = row.get_active()
        try:
            if wanted:
                autostart.enable()
            else:
                autostart.disable()
        except autostart.AutostartError as exc:
            self.toast(str(exc))
        else:
            self.toast("Will start at login" if wanted else "Will not start at login")
        self._sync_startup_row()

    def _add_account_group(self, account: Account) -> None:
        group = Adw.PreferencesGroup()
        group.set_title(account.label)
        group.set_description(account.principal)

        edit = Gtk.Button(label="Edit")
        edit.add_css_class("flat")
        edit.connect("clicked", lambda *_: self.open_editor(account))
        group.set_header_suffix(edit)

        # State row: icon + badge + the one action that matters right now.
        state_row = Adw.ActionRow(title="Ticket")
        image = Gtk.Image.new_from_pixbuf(icons.gdk_pixbuf_for_state("none", 20))
        state_row.add_prefix(image)

        badge = Gtk.Label(label="…")
        badge.add_css_class("krb-badge")
        state_row.add_suffix(badge)

        action_button = Gtk.Button(label="Renew")
        action_button.add_css_class("suggested-action")
        action_button.set_valign(Gtk.Align.CENTER)
        state_row.add_suffix(action_button)
        action_button.connect("clicked", lambda *_: self.renew(account))
        group.add(state_row)

        details = Adw.ActionRow(title="Details")
        details.set_subtitle("")
        show = Gtk.Button(label="Show")
        show.set_valign(Gtk.Align.CENTER)
        show.add_css_class("flat")
        show.connect("clicked", lambda *_: self._show_text(f"klist — {account.principal}", _ticket_lines(account)))
        details.add_suffix(show)
        group.add(details)

        destroy = Adw.ActionRow(title="Destroy ticket", subtitle="Sign out of this account")
        destroy_button = Gtk.Button(label="Destroy")
        destroy_button.set_valign(Gtk.Align.CENTER)
        destroy_button.add_css_class("destructive-action")
        destroy_button.connect("clicked", lambda *_: self.destroy(account))
        destroy.add_suffix(destroy_button)
        group.add(destroy)

        remove = Adw.ActionRow(title="Remove account", subtitle="Forget this account in the app")
        remove_button = Gtk.Button(icon_name="user-trash-symbolic")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.add_css_class("flat")
        remove_button.connect("clicked", lambda *_: self.remove(account))
        remove.add_suffix(remove_button)
        group.add(remove)

        self._page.add(group)
        self._groups[account.id] = group

        # Stash the widgets that the poller updates.
        group._badge = badge  # type: ignore[attr-defined]
        group._image = image  # type: ignore[attr-defined]
        group._details = details  # type: ignore[attr-defined]
        group._button = action_button  # type: ignore[attr-defined]

        self._render_state(account, self._statuses.get(account.id))

    def set_statuses(self, statuses: dict[str, kerberos.Status]) -> None:
        self._statuses = statuses
        for account in self.store.accounts:
            self._render_state(account, statuses.get(account.id))
        # The CLI can change autostart while this window is open; re-read it on
        # each poll so the switch never lies about what login will do.
        self._sync_startup_row()

    def _render_state(self, account: Account, status: Optional[kerberos.Status]) -> None:
        group = self._groups.get(account.id)
        if group is None:
            return
        badge: Gtk.Label = group._badge  # type: ignore[attr-defined]
        image: Gtk.Image = group._image  # type: ignore[attr-defined]
        details: Adw.ActionRow = group._details  # type: ignore[attr-defined]
        button: Gtk.Button = group._button  # type: ignore[attr-defined]

        state = status.state if status else "none"
        badge.set_label(status.label if status else "…")
        for css in ("krb-valid", "krb-expiring", "krb-expired", "krb-none", "krb-error"):
            badge.remove_css_class(css)
        badge.add_css_class(f"krb-{state}")
        image.set_from_pixbuf(icons.gdk_pixbuf_for_state(state, 20))

        details.set_subtitle(status.detail if status else "Reading ticket…")
        has_ticket = status is not None and status.state not in ("none", "error")
        button.set_label("Renew" if has_ticket else "Sign in")
        button.set_sensitive(True)

    # ── actions ───────────────────────────────────────────────

    def open_editor(self, account: Optional[Account]) -> None:
        def saved(new_account: Account) -> None:
            self.store.add(new_account)
            self.rebuild()
            self.toast(f"Saved {new_account.principal}")

        EditorWindow(self, account, on_save=saved).present()

    def renew(self, account: Account, *, quiet: bool = False) -> None:
        def work():
            if account.method == METHOD_KEYTAB:
                kerberos.kinit_from_keytab(account.principal, account.keytab, account.cache or None)
            elif account.method == METHOD_SESSION:
                raise kerberos.KerberosError(
                    "This ticket comes from your login session",
                    hint="SSSD renews it automatically — log out and back in if it is expired",
                )
            else:
                password = secrets.lookup_password(account.principal)
                if not password:
                    raise kerberos.KerberosError(
                        "No stored password",
                        hint="open Edit and set one, or use a keytab",
                    )
                kerberos.kinit_with_password(account.principal, password, account.cache or None)
            return kerberos.status(account.cache or None, warn_minutes=account.warn_minutes)

        def ok(status):
            self._statuses[account.id] = status  # type: ignore[assignment]
            self._render_state(account, status)  # type: ignore[arg-type]
            if not quiet:
                self.toast(f"Renewed — {status.label} left")  # type: ignore[attr-defined]

        def fail(exc):
            message = getattr(exc, "friendly", str(exc))
            if not quiet:
                self.toast(message)
            print(f"krbctl: renew {account.principal}: {message}", flush=True)

        run_async(work, ok, fail)

    def destroy(self, account: Account) -> None:
        def work():
            kerberos.destroy(account.cache or None)
            return kerberos.status(account.cache or None, warn_minutes=account.warn_minutes)

        def ok(status):
            self._statuses[account.id] = status  # type: ignore[assignment]
            self._render_state(account, status)  # type: ignore[arg-type]
            self.toast("Ticket destroyed")

        run_async(work, ok, lambda exc: self.toast(getattr(exc, "friendly", str(exc))))

    def remove(self, account: Account) -> None:
        dialog = Adw.AlertDialog.new(
            f"Remove {account.label}?",
            "The app forgets this account and any stored password. The ticket itself is left alone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "remove":
                return
            if account.remember_password:
                secrets.forget_password(account.principal)
            self.store.remove(account.id)
            self._statuses.pop(account.id, None)
            self.rebuild()
            self.toast("Account removed")

        dialog.connect("response", on_response)
        dialog.present(self)
