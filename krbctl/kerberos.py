"""Kerberos primitives for krbctl.

Deliberately GUI-free and front-end agnostic: everything here shells out to the
MIT krb5 client tools (``klist`` / ``kinit`` / ``kdestroy`` / ``ktutil``) and
parses their output. That keeps one brain usable from the GTK window, the tray
icon, the CLI, and the Noctalia bar widget.

Rules that matter and are easy to get wrong:

* Every subprocess runs with ``LC_ALL=C``. ``klist`` formats dates with
  ``strftime("%x")``, so under a Greek desktop locale you get ``07/11/2026``
  (dd/mm/yyyy) and under ``C`` you get ``11/07/2026`` (mm/dd/yyyy). We pin the
  locale so the parser is deterministic instead of guessing per user.
* The TGT expiry is the **last** timestamp on the ``krbtgt/...`` row, not the
  first, and not the timestamp of whichever service ticket comes later.
* Passwords travel over stdin only (``--password-file=STDIN``), never argv,
  so they never show up in ``ps``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

KLIST = "klist"
KINIT = "kinit"
KDESTROY = "kdestroy"
KTUTIL = "ktutil"

DEFAULT_WARN_MINUTES = 15

# Enc types offered when building a keytab from a password. AD accepts all of
# these; aes256 first so it wins where the account supports it.
DEFAULT_ENCTYPES = (
    "aes256-cts-hmac-sha1-96",
    "aes128-cts-hmac-sha1-96",
)


class KerberosError(Exception):
    """A krb5 client tool failed in a way the user should hear about."""

    def __init__(self, message: str, hint: Optional[str] = None, raw: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.raw = raw.strip()

    @property
    def friendly(self) -> str:
        """One-line message suitable for a toast or a notification body."""
        return f"{self.message}. {self.hint}" if self.hint else self.message


@dataclass
class Ticket:
    """The TGT sitting in one credential cache."""

    cache: str
    principal: str
    started: Optional[datetime] = None
    expires: Optional[datetime] = None
    renew_until: Optional[datetime] = None
    services: list[str] = field(default_factory=list)

    @property
    def tgt(self) -> Optional[str]:
        for svc in self.services:
            if svc.startswith("krbtgt/"):
                return svc
        return None


@dataclass
class Status:
    """Result of looking at one account's cache."""

    state: str  # valid | expiring | expired | none | error
    ticket: Optional[Ticket] = None
    seconds_left: Optional[int] = None
    message: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.state in ("valid", "expiring")

    @property
    def label(self) -> str:
        """Short badge text, e.g. ``9h42m`` / ``expired`` / ``no tgt``."""
        if self.state == "valid" or self.state == "expiring":
            return humanize(self.seconds_left)
        if self.state == "expired":
            return "expired"
        if self.state == "none":
            return "no tgt"
        return "err"

    @property
    def detail(self) -> str:
        if self.state == "none":
            return "No ticket in this cache"
        if self.state == "error":
            return self.message or "Cannot read credential cache"
        if self.state == "expired":
            when = self.ticket.expires if self.ticket else None
            return f"Expired {format_dt(when)}" if when else "Expired"
        parts = []
        if self.ticket and self.ticket.expires:
            parts.append(f"Expires {format_dt(self.ticket.expires)}")
        if self.ticket and self.ticket.renew_until:
            parts.append(f"renewable until {format_dt(self.ticket.renew_until)}")
        return " · ".join(parts)


# ───────────────────────────── helpers ─────────────────────────────

def expand(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    return os.path.expanduser(os.path.expandvars(path))


def humanize(seconds: Optional[int]) -> str:
    """``34920`` -> ``9h42m``. Never longer than ~8 chars, it goes on a bar."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "expired"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h" if hours else f"{days}d"


def format_dt(value: Optional[datetime]) -> str:
    return value.strftime("%a %d %b %H:%M") if value else "?"


def split_principal(principal: str) -> tuple[str, str]:
    """``user@REALM`` -> ``("user", "REALM")``; third field empty if absent."""
    principal = (principal or "").strip()
    if "@" in principal:
        user, realm = principal.rsplit("@", 1)
        return user, realm.upper()
    return principal, ""


def default_realm() -> str:
    """Best-effort realm from /etc/krb5.conf, used only to prefill a form."""
    try:
        with open("/etc/krb5.conf", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("default_realm"):
                    _, _, value = stripped.partition("=")
                    return value.strip().upper()
    except OSError:
        pass
    return ""


def have_tools() -> list[str]:
    """Return the krb5 tools that are missing from PATH."""
    return [tool for tool in (KLIST, KINIT, KDESTROY, KTUTIL) if not shutil.which(tool)]


def _run(
    argv: list[str],
    *,
    cache: Optional[str] = None,
    stdin_text: Optional[str] = None,
    timeout: int = 45,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("KRB5_TRACE", None)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    cache = expand(cache)
    if cache:
        env["KRB5CCNAME"] = cache
    else:
        # Respect whatever the session default is (KCM:uid, KEYRING:...).
        env.pop("KRB5CCNAME", None) if not os.environ.get("KRB5CCNAME") else None
    try:
        return subprocess.run(
            argv,
            env=env,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise KerberosError(
            f"{argv[0]} not found",
            hint="install the krb5-workstation package",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise KerberosError(
            f"{argv[0]} timed out after {timeout}s",
            hint="the KDC is not answering — is the VPN / Twingate connected?",
        ) from exc


# ───────────────────────────── parsing ─────────────────────────────

_ROW_RE = re.compile(
    r"^(?P<started>\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<expires>\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<service>\S.*)$"
)
_RENEW_RE = re.compile(r"^\s+renew until (?P<ts>\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2}:\d{2})")
_CACHE_RE = re.compile(r"^Ticket cache:\s*(?P<cache>.+?)\s*$")
_PRINCIPAL_RE = re.compile(r"^Default principal:\s*(?P<principal>.+?)\s*$")

_NO_CACHE_MARKERS = (
    "no credentials cache found",
    "credentials cache file",
    "cache not found",
    "no credentials cache",
    "ticket cache not found",
)


def _ts(text: str) -> Optional[datetime]:
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_klist(text: str, cache_hint: str = "") -> Optional[Ticket]:
    """Parse ``klist`` output. Returns ``None`` when the cache is empty/absent."""
    if _looks_like_missing_cache(text):
        return None

    ticket = Ticket(cache=cache_hint, principal="")
    saw_row = False
    last_index: Optional[int] = None

    for line in text.splitlines():
        if match := _CACHE_RE.match(line):
            ticket.cache = match.group("cache")
            continue
        if match := _PRINCIPAL_RE.match(line):
            ticket.principal = match.group("principal")
            continue
        if match := _RENEW_RE.match(line):
            if last_index is not None:
                ticket.services[last_index] += " [renew]"  # marker, stripped below
                ticket.renew_until = _ts(match.group("ts"))
            continue
        if match := _ROW_RE.match(line):
            started = _ts(match.group("started"))
            expires = _ts(match.group("expires"))
            service = match.group("service").strip()
            if service.startswith("krbtgt/") and ticket.expires is None:
                ticket.started = started
                ticket.expires = expires
            ticket.services.append(service)
            last_index = len(ticket.services) - 1
            saw_row = True

    # Drop the internal marker tags added while walking renew-until lines.
    ticket.services = [svc.replace(" [renew]", "") for svc in ticket.services]

    if not saw_row:
        return None
    return ticket


def _looks_like_missing_cache(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _NO_CACHE_MARKERS)


# ───────────────────────────── queries ─────────────────────────────

def read_ticket(cache: Optional[str] = None) -> Optional[Ticket]:
    """Return the TGT in ``cache`` (or the session default) — ``None`` if none."""
    target = expand(cache)
    result = _run([KLIST, "-c", target] if target else [KLIST])
    if result.returncode != 0:
        if _looks_like_missing_cache(result.stderr) or _looks_like_missing_cache(result.stdout):
            return None
        raise KerberosError(
            "Cannot read the credential cache",
            hint=(result.stderr or result.stdout).strip().splitlines()[-1][:160]
            if (result.stderr or result.stdout).strip()
            else None,
            raw=result.stderr or result.stdout,
        )
    return parse_klist(result.stdout + result.stderr, cache or "")


def list_caches() -> list[tuple[str, str]]:
    """``klist -l`` -> [(principal, cache)]. Empty list if no caches exist."""
    result = _run([KLIST, "-l"])
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        # Skip blanks, the header, and the dotted/dashed rule under it.
        if not stripped or stripped.startswith("Principal name") or set(stripped) <= set("- "):
            continue
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) >= 2:
            entries.append((parts[0], parts[1]))
    return entries


def status(
    cache: Optional[str] = None,
    *,
    warn_minutes: int = DEFAULT_WARN_MINUTES,
) -> Status:
    """Classify one cache into valid / expiring / expired / none / error."""
    try:
        ticket = read_ticket(cache)
    except KerberosError as exc:
        return Status(state="error", message=exc.friendly, raw=exc.raw)

    if ticket is None or ticket.expires is None:
        return Status(state="none")

    seconds_left = int((ticket.expires - datetime.now()).total_seconds())
    if seconds_left <= 0:
        return Status(state="expired", ticket=ticket, seconds_left=seconds_left)
    if seconds_left <= max(0, warn_minutes) * 60:
        return Status(state="expiring", ticket=ticket, seconds_left=seconds_left)
    return Status(state="valid", ticket=ticket, seconds_left=seconds_left)


# ───────────────────────────── actions ─────────────────────────────

def _raise_from(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode == 0:
        return
    raw = (result.stderr or result.stdout or "").strip()
    lowered = raw.lower()

    if "cannot contact any kdc" in lowered or "cannot resolve" in lowered or "no route to host" in lowered:
        raise KerberosError(
            f"{action} failed: cannot reach the KDC",
            hint="is the VPN / Twingate connected?",
            raw=raw,
        )
    if "preauthentication failed" in lowered or "password incorrect" in lowered:
        raise KerberosError(f"{action} failed: wrong password", raw=raw)
    if "client not found in kerberos database" in lowered or "principal unknown" in lowered:
        raise KerberosError(
            f"{action} failed: unknown principal",
            hint="check the spelling and the realm",
            raw=raw,
        )
    if "keytab" in lowered and ("not found" in lowered or "no such file" in lowered):
        raise KerberosError(
            f"{action} failed: keytab missing or unreadable",
            hint="check the keytab path and its permissions",
            raw=raw,
        )
    if "clock skew" in lowered:
        raise KerberosError(f"{action} failed: clock skew", hint="the local clock is off", raw=raw)
    if "ticket expired" in lowered or "tgt expired" in lowered:
        raise KerberosError(f"{action} failed: ticket expired", raw=raw)

    tail = raw.splitlines()[-1][:200] if raw else f"exit code {result.returncode}"
    raise KerberosError(f"{action} failed", hint=tail, raw=raw)


def kinit_from_keytab(
    principal: str,
    keytab: str,
    cache: Optional[str] = None,
    *,
    timeout: int = 60,
) -> None:
    keytab = expand(keytab) or ""
    if not os.path.exists(keytab):
        raise KerberosError(
            "Keytab not found",
            hint=f"{keytab} does not exist",
        )
    result = _run(
        [KINIT, "-k", "-t", keytab, principal],
        cache=cache,
        timeout=timeout,
    )
    _raise_from(result, "Renew")


def kinit_with_password(
    principal: str,
    password: str,
    cache: Optional[str] = None,
    *,
    timeout: int = 60,
) -> None:
    result = _run(
        [KINIT, "--password-file=STDIN", principal],
        cache=cache,
        stdin_text=f"{password}\n",
        timeout=timeout,
    )
    _raise_from(result, "Sign in")


def destroy(cache: Optional[str] = None) -> None:
    target = expand(cache)
    result = _run([KDESTROY, "-c", target] if target else [KDESTROY])
    _raise_from(result, "Destroy")


def create_keytab(
    principal: str,
    password: str,
    path: str,
    *,
    enctypes: Iterable[str] = DEFAULT_ENCTYPES,
    kvno: int = 1,
    timeout: int = 60,
) -> str:
    """Build a keytab for ``principal`` from its password via ``ktutil``.

    The password is fed on stdin alongside the ktutil script; it never appears
    in argv. ``ktutil`` still asks the KDC for the salt, so this needs a
    reachable KDC (i.e. VPN up) and a correct password.
    """
    path = expand(path) or ""
    script: list[str] = []
    for enctype in enctypes:
        script.append(f"addent -password -p {principal} -k {kvno} -e {enctype}")
        script.append(password)
    script.append(f"wkt {path}")
    script.append("quit")

    result = _run([KTUTIL], stdin_text="\n".join(script) + "\n", timeout=timeout)
    _raise_from(result, "Create keytab")

    if not os.path.exists(path):
        raise KerberosError("ktutil did not write the keytab", hint=path, raw=result.stdout)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def keytab_principals(path: str) -> list[str]:
    """Principals listed in a keytab (used to validate what the user typed)."""
    result = _run([KLIST, "-k", "-t", expand(path) or ""])
    if result.returncode != 0:
        return []
    principals = []
    for line in result.stdout.splitlines():
        # Columns are KVNO [Timestamp] Principal; the timestamp only appears
        # with -t, and older krb5 builds print a different header layout.
        match = re.match(
            r"^\s+\d+\s+(?:\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}:\d{2}\s+)?(\S+@\S+)\s*$",
            line,
        )
        if match and match.group(1) not in principals:
            principals.append(match.group(1))
    return principals
