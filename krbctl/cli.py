"""Command line interface for krbctl.

The CLI is a first-class front-end, not an afterthought: the tray icon, the GTK
window and external consumers (Noctalia bar widget, Waybar, scripts) all read
the same ``--json`` status output. It has no GTK dependency so it works over SSH
and in cron jobs.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Optional

from . import config, kerberos, secrets
from .config import Account, METHOD_KEYTAB

EXIT_OK = 0
EXIT_WARN = 1  # expiring / expired / no ticket
EXIT_ERROR = 2


def _status_payload(store: config.Store, only: Optional[str] = None) -> dict:
    rows = []
    severity = {"error": 4, "expired": 3, "none": 2, "expiring": 1, "valid": 0}
    worst = "valid"

    for account in store.accounts:
        if only and only not in (account.id, account.principal):
            continue
        state = kerberos.status(account.cache or None, warn_minutes=account.warn_minutes)
        ticket = state.ticket
        rows.append(
            {
                "id": account.id,
                "label": account.label,
                "principal": account.principal,
                "method": account.method,
                "state": state.state,
                "badge": state.label,
                "detail": state.detail,
                "seconds_left": state.seconds_left,
                "expires": ticket.expires.isoformat() if ticket and ticket.expires else None,
                "renew_until": ticket.renew_until.isoformat()
                if ticket and ticket.renew_until
                else None,
                "cache": (ticket.cache if ticket and ticket.cache else account.cache) or "",
                "auto_renew": account.auto_renew,
                "keytab": kerberos.expand(account.keytab) if account.method == METHOD_KEYTAB else "",
                "error": state.message if state.state == "error" else None,
            }
        )
        if severity[state.state] > severity[worst]:
            worst = state.state

    return {"accounts": rows, "worst": worst, "count": len(rows)}


def cmd_status(args: argparse.Namespace) -> int:
    store = config.Store().load()
    if not store.accounts:
        if args.json:
            print(json.dumps({"accounts": [], "worst": "none", "count": 0}))
        else:
            print("No Kerberos accounts configured. Run: krbctl gui")
        return EXIT_WARN

    payload = _status_payload(store, args.account)
    if args.json:
        print(json.dumps(payload, indent=None if args.compact else 2))
    else:
        for row in payload["accounts"]:
            line = f"{row['badge']:<9} {row['label']:<22} {row['principal']}"
            if row["detail"]:
                line += f"  ({row['detail']})"
            print(line)
        if len(payload["accounts"]) > 1:
            print(f"\nworst: {payload['worst']}")

    if payload["worst"] == "error":
        return EXIT_ERROR
    if payload["worst"] in ("expired", "none", "expiring"):
        return EXIT_WARN
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    store = config.Store().load()
    if args.json:
        print(json.dumps([account.to_dict() for account in store.accounts], indent=2))
        return EXIT_OK
    if not store.accounts:
        print("No accounts configured.")
        return EXIT_WARN
    for account in store.accounts:
        extra = (
            f"keytab={account.keytab}"
            if account.method == METHOD_KEYTAB
            else "password (keyring)" if account.remember_password else "password (ask)"
        )
        auto = "auto-renew" if account.auto_renew else "manual"
        print(f"{account.id:<26} {account.principal:<34} {account.method:<8} {auto:<11} {extra}")
    return EXIT_OK


def _resolve(store: config.Store, identifier: Optional[str]) -> Optional[Account]:
    """Pick an account by id, by principal, or the only one there is."""
    if not store.accounts:
        print("No accounts configured. Run: krbctl gui", file=sys.stderr)
        return None
    if identifier:
        for account in store.accounts:
            if identifier in (account.id, account.principal):
                return account
        print(f"No such account: {identifier}", file=sys.stderr)
        return None
    if len(store.accounts) == 1:
        return store.accounts[0]
    print("Several accounts configured — pass --account <id>:", file=sys.stderr)
    for account in store.accounts:
        print(f"  {account.id}", file=sys.stderr)
    return None


def cmd_renew(args: argparse.Namespace) -> int:
    store = config.Store().load()
    account = _resolve(store, args.account)
    if account is None:
        return EXIT_ERROR

    try:
        if account.method == METHOD_KEYTAB:
            kerberos.kinit_from_keytab(account.principal, account.keytab, account.cache or None)
        else:
            password = None
            if not args.password_stdin:
                password = secrets.lookup_password(account.principal)
            if not password:
                if args.quiet or not sys.stdin.isatty():
                    print(
                        f"No stored password for {account.principal}. Sign in from the app window.",
                        file=sys.stderr,
                    )
                    return EXIT_ERROR
                password = getpass.getpass(f"Password for {account.principal}: ")
            kerberos.kinit_with_password(account.principal, password, account.cache or None)
    except kerberos.KerberosError as exc:
        print(exc.friendly, file=sys.stderr)
        return EXIT_ERROR

    state = kerberos.status(account.cache or None, warn_minutes=account.warn_minutes)
    if args.json:
        print(json.dumps(_status_payload(store, account.id)))
    elif not args.quiet:
        print(f"Renewed {account.principal} — {state.label} left ({state.detail})")
    return EXIT_OK


def cmd_destroy(args: argparse.Namespace) -> int:
    store = config.Store().load()
    account = _resolve(store, args.account)
    if account is None:
        return EXIT_ERROR
    try:
        kerberos.destroy(account.cache or None)
    except kerberos.KerberosError as exc:
        print(exc.friendly, file=sys.stderr)
        return EXIT_ERROR
    if not args.quiet:
        print(f"Destroyed ticket for {account.principal}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Explain what is and isn't ready — the first thing to run on a new box."""
    problems = 0
    print("krb5 client tools")
    missing = kerberos.have_tools()
    for tool in ("klist", "kinit", "kdestroy", "ktutil"):
        status = "MISSING" if tool in missing else "ok"
        if tool in missing:
            problems += 1
        print(f"  {tool:<10} {status}")
    if missing:
        print("  -> sudo dnf install krb5-workstation")

    print("\nconfiguration")
    store = config.Store().load()
    state = "present" if store.path.exists() else "not written yet"
    print(f"  accounts.json  {state}  {store.path}")
    print(f"  accounts       {len(store.accounts)}")
    if not store.accounts:
        print("  -> no keytab detected; open the window and add an account")

    print("\nsecrets")
    print(f"  libsecret      {'available' if secrets.available() else 'MISSING (python3-secret)'}")

    print("\ncaches")
    caches = kerberos.list_caches()
    if not caches:
        print("  none")
    for principal, cache in caches:
        print(f"  {cache:<24} {principal}")

    print("\naccounts")
    for account in store.accounts:
        if account.method == METHOD_KEYTAB:
            path = kerberos.expand(account.keytab) or ""
            import os as _os

            if not _os.path.exists(path):
                print(f"  {account.id}: keytab missing -> {path}")
                problems += 1
                continue
            perms = oct(_os.stat(path).st_mode & 0o777)
            note = "" if perms == "0o600" else f"  (permissions {perms}, want 0o600)"
            print(f"  {account.id}: keytab {path}{note}")
        else:
            stored = "stored" if secrets.lookup_password(account.principal) else "not stored"
            print(f"  {account.id}: password {stored}")

    print("\n" + ("All good." if problems == 0 else f"{problems} problem(s) found."))
    return EXIT_OK if problems == 0 else EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="krbctl",
        description="Kerberos ticket manager with a tray icon and a GTK window.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="show ticket state")
    p_status.add_argument("--json", action="store_true", help="machine-readable output")
    p_status.add_argument("--compact", action="store_true", help="single-line JSON")
    p_status.add_argument("--account", help="limit to one account id")
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="list configured accounts")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_renew = sub.add_parser("renew", help="get (or renew) a ticket")
    p_renew.add_argument("--account")
    p_renew.add_argument("--json", action="store_true")
    p_renew.add_argument("--quiet", "-q", action="store_true")
    p_renew.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of the keyring",
    )
    p_renew.set_defaults(func=cmd_renew)

    p_destroy = sub.add_parser("destroy", help="destroy the ticket")
    p_destroy.add_argument("--account")
    p_destroy.add_argument("--quiet", "-q", action="store_true")
    p_destroy.set_defaults(func=cmd_destroy)

    p_doctor = sub.add_parser("doctor", help="diagnose the setup")
    p_doctor.set_defaults(func=cmd_doctor)

    p_tray = sub.add_parser("tray", help="run the tray icon")
    p_tray.add_argument("--window", action="store_true", help="open the window at start")
    p_tray.set_defaults(func=lambda a: _gui_run(tray=True, show_window=a.window))

    p_gui = sub.add_parser("gui", help="open the settings window")
    p_gui.set_defaults(func=lambda a: _gui_run(tray=True, show_window=True))

    return parser


def _gui_run(*, tray: bool, show_window: bool) -> int:
    """Import GTK lazily so all the non-GUI commands work without it."""
    from .app import main as app_main

    return app_main(tray=tray, show_window=show_window)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from . import __version__

        print(f"krbctl {__version__}")
        return EXIT_OK

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
