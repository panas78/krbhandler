"""Start-at-login, per the XDG Autostart spec.

One entry at ``$XDG_CONFIG_HOME/autostart/krbctl-tray.desktop``. The desktop
environment reads it on login and runs the tray.

Turning it *off* writes ``Hidden=true`` rather than deleting the file. That is
the spec's masking mechanism: a ``Hidden`` entry overrides any same-named entry
further up the search path, so disabling still works if krbctl was ever
installed system-wide into ``/etc/xdg/autostart``. Deleting the user's file
would silently re-enable the system one.

This module is deliberately GUI-free so ``install.sh`` and the CLI can both use
it, and so it is testable without a display.
"""

from __future__ import annotations

import os
import shutil
from configparser import RawConfigParser
from pathlib import Path
from typing import Optional

from . import APP_NAME

ENTRY_NAME = "krbctl-tray.desktop"
SECTION = "Desktop Entry"

# Keys that make the entry invisible in menus: it is a login hook, not something
# to launch by hand (the launcher entry is io.github.pan.Krbctl.desktop).
_ENTRY_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name={name} (tray)
GenericName=Kerberos Ticket Manager
Comment=Kerberos ticket tray icon for Noctalia, Waybar, KDE and GNOME AppIndicator
Exec={binary} tray
TryExec={binary}
Icon=krbctl
Terminal=false
NoDisplay=true
Categories=System;Security;
X-GNOME-Autostart-enabled={gnome_enabled}
X-GNOME-UsesNotifications=true
{hidden_line}"""


class AutostartError(RuntimeError):
    """Raised when the entry cannot be written or the launcher cannot be found."""


def autostart_dir() -> Path:
    """``$XDG_CONFIG_HOME/autostart``, or ``~/.config/autostart``."""
    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(home) / "autostart"


def entry_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def launcher() -> Optional[str]:
    """The installed ``krbctl`` executable, or ``None`` if it cannot be found.

    Checked in this order:

    1. The nearest ``bin/krbctl`` above this package. Installed, the package
       lives at ``$PREFIX/share/krbctl/krbctl/``, so walking up finds
       ``$PREFIX/bin/krbctl`` — deterministic, and correct even when
       ``~/.local/bin`` is not on the caller's ``PATH`` (which is the case while
       ``install.sh`` is still running, and when installing to a custom PREFIX).
    2. ``krbctl`` on ``PATH``. Covers running from a source checkout.

    Walking up beats counting parent directories: the count depends on the
    install layout (``$PREFIX/share/krbctl/krbctl/``), and getting it wrong does
    not fail — it silently returns a *different* krbctl off ``PATH``, so a
    custom-PREFIX install would write an autostart entry for the default one.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "bin" / "krbctl"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("krbctl")
    return found or None


def _entry_text(binary: str, *, enabled: bool = True, hidden: bool = False) -> str:
    return _ENTRY_TEMPLATE.format(
        name=APP_NAME,
        binary=binary,
        gnome_enabled="true" if enabled else "false",
        hidden_line="Hidden=true\n" if hidden else "",
    )


def read_entry(path: Optional[Path] = None) -> dict[str, str]:
    """The ``[Desktop Entry]`` keys of an autostart file, or ``{}`` if unreadable."""
    target = path or entry_path()
    parser = RawConfigParser(interpolation=None, strict=False)
    # Keep key case: `X-GNOME-Autostart-enabled` is the key, `x-gnome-...` is not.
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read(target, encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not parser.has_section(SECTION):
        return {}
    return dict(parser.items(SECTION))


def _flag(entry: dict[str, str], key: str, *, default: bool) -> bool:
    """XDG booleans are ``true``/``false``, and commonly ``1``/``0`` too."""
    raw = entry.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def entry_binary(entry: dict[str, str]) -> Optional[str]:
    """The executable an entry runs — the first word of its ``Exec`` line."""
    exec_line = entry.get("Exec", "").strip()
    return exec_line.split()[0] if exec_line else None


def is_enabled(path: Optional[Path] = None) -> bool:
    """Whether login will actually start the tray.

    A file that exists but says ``Hidden=true`` (or the GNOME variant of the
    same thing) is *off* — the redirection is the point of those keys, so
    existence alone would misreport it.
    """
    target = path or entry_path()
    if not target.exists():
        return False
    entry = read_entry(target)
    if not entry:
        return False
    if _flag(entry, "Hidden", default=False):
        return False
    return _flag(entry, "X-GNOME-Autostart-enabled", default=True)


def enable(binary: Optional[str] = None) -> Path:
    """Write the entry, replacing any previous one. Returns its path."""
    target_binary = binary or launcher()
    if not target_binary:
        raise AutostartError(
            "cannot find the krbctl launcher; install it first (./install.sh)"
        )
    path = entry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_entry_text(target_binary), encoding="utf-8")
    except OSError as exc:
        raise AutostartError(f"cannot write {path}: {exc}") from exc
    return path


def disable() -> bool:
    """Mask the entry with ``Hidden=true``. Returns whether anything changed."""
    path = entry_path()
    if not path.exists():
        return False
    # Keep whatever Exec is already there: if this machine's launcher has since
    # moved, re-deriving it could point the disabled entry somewhere new, and
    # re-enabling later should restore what the user actually had.
    existing = read_entry(path)
    binary = entry_binary(existing) or launcher() or "krbctl"
    try:
        path.write_text(_entry_text(binary, enabled=False, hidden=True), encoding="utf-8")
    except OSError as exc:
        raise AutostartError(f"cannot write {path}: {exc}") from exc
    return True


def status() -> dict:
    """Everything a UI needs to render the switch honestly."""
    path = entry_path()
    entry = read_entry(path) if path.exists() else {}
    binary = entry_binary(entry) or launcher()
    return {
        "enabled": is_enabled(path),
        "path": str(path),
        "exists": path.exists(),
        "binary": binary or "",
        # An entry pointing at a launcher that has been moved or removed is a
        # silent no-op at login, which looks exactly like the feature not working.
        "binary_exists": bool(binary and Path(binary).exists()),
    }
