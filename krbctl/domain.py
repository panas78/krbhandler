"""Joining the machine to an Active Directory domain.

This is the other half of what krbctl is for. Kerberos tickets only exist after
a machine is joined, so the join belongs in the same app rather than in a shell
script the user has to remember to run first.

The engine is ``realm`` (realmd), the same tool the distro ships and that GNOME's
own Enterprise Login uses. It does the whole job — writes ``sssd.conf``, runs
``authselect``, discovers the domain over DNS SRV records — and doing any of that
by hand would be reinventing it badly.

Two things make this workable without a terminal:

* **The password never reaches realmd.** ``realm join`` would prompt for it, and
  there is no flag to feed it one. Instead the app takes the password, uses it to
  get a Kerberos ticket for the joining account (``kinit``, password on stdin),
  and then joins with ``--no-password --unattended`` so realmd authenticates with
  that existing ticket. The ticket lives in a throwaway FILE cache because the
  privileged process runs as root and cannot reach the user's KCM session cache.
* **Root is obtained when it is needed, not before.** ``realm join`` and
  ``realm permit`` need root; everything else here does not. Privileged calls go
  through ``pkexec`` so the desktop shows its normal password prompt, falling
  back to ``sudo -n`` where that is configured.

Nothing in this module imports GTK: the CLI does joins over SSH and the tests run
without a display.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from . import kerberos

REALM = "realm"
RPM = "rpm"
AUTHSELECT = "authselect"
SYSTEMCTL = "systemctl"

# What bootstrap-system.sh installs. realmd also reports these itself in
# `realm discover`, which is the better source — but only once a realm is
# reachable, and a fresh machine usually cannot reach it yet.
REQUIRED_PACKAGES = (
    "realmd",
    "sssd",
    "sssd-ad",
    "adcli",
    "krb5-workstation",
    "oddjob",
    "oddjob-mkhomedir",
    "samba-common-tools",
)

# Packages that are only needed for specific features.
OPTIONAL_PACKAGES = ("pcsc-lite",)  # smartcard logon

# pkexec exit codes. 126 is "dismissed or not authorized", 127 is "not
# authorized or the authentication failed"; both mean the same thing to a user:
# nobody approved the operation, so nothing happened.
_PKEXEC_DENIED = (126, 127)

# Which systemd unit proves an identity client is installed. Used to tell a real
# membership from a config file that realmd found and described.
CLIENT_UNITS = {"sssd": "sssd.service", "winbind": "winbind.service"}


class DomainError(RuntimeError):
    """A domain operation failed. ``hint`` carries the next step for the user."""

    def __init__(self, message: str, *, hint: str = "", output: str = ""):
        super().__init__(message)
        self.hint = hint
        self.output = output

    @property
    def friendly(self) -> str:
        return f"{self} — {self.hint}" if self.hint else str(self)


@dataclass
class Check:
    """One line of the readiness report, like check-domain.sh prints."""

    name: str
    ok: bool
    detail: str = ""
    # "required" failures block a join; "warning" ones are worth knowing.
    severity: str = "required"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass
class RealmInfo:
    """What ``realm discover`` reported about a realm."""

    name: str
    configured: bool = False
    server_software: str = ""
    client_software: str = ""
    required_packages: list[str] = field(default_factory=list)
    domain_name: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "configured": self.configured,
            "server_software": self.server_software,
            "client_software": self.client_software,
            "required_packages": list(self.required_packages),
            "domain_name": self.domain_name,
        }


# ───────────────────────────── plumbing ─────────────────────────────


def _run(
    argv: list[str],
    *,
    stdin_text: Optional[str] = None,
    timeout: int = 120,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a command with a predictable locale.

    English output matters here: every parse below keys off words like
    ``configured:`` and ``required-package:``, and a localised realmd would
    silently change them.
    """
    full_env = dict(os.environ)
    full_env["LC_ALL"] = "C"
    full_env["LANG"] = "C"
    if env:
        full_env.update(env)
    try:
        return subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=full_env,
        )
    except FileNotFoundError as exc:
        raise DomainError(
            f"{argv[0]} not found",
            hint=f"install the package that provides {argv[0]}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DomainError(
            f"{argv[0]} timed out after {timeout}s",
            hint="the domain controller is not answering — is the VPN connected?",
        ) from exc


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def privilege_tool() -> Optional[str]:
    """What we can use to become root, if anything.

    ``pkexec`` first: it raises the desktop's own authentication dialog, so the
    user never types a password into a terminal, and it needs no sudoers entry.
    """
    if _have("pkexec"):
        return "pkexec"
    if _have("sudo"):
        # Only usable unattended if it will not prompt.
        probe = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
        if probe.returncode == 0:
            return "sudo"
    return None


def _privileged(
    argv: list[str],
    *,
    env: Optional[dict[str, str]] = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run ``argv`` as root, via pkexec or non-interactive sudo.

    The password is never passed here — only ever through a Kerberos cache file
    that this process created, so there is nothing secret in the command line.
    """
    tool = privilege_tool()
    if tool is None:
        raise DomainError(
            "Cannot obtain administrator rights",
            hint="install polkit (pkexec), or configure sudo for your user",
        )

    command = list(argv)
    if env:
        # pkexec scrubs the environment, so the variables have to be set by a
        # child `env` instead. KRB5CCNAME is how realmd finds the ticket.
        command = ["env"] + [f"{key}={value}" for key, value in env.items()] + command
    command = [tool] + command

    result = _run(command, timeout=timeout)
    if result.returncode in _PKEXEC_DENIED:
        raise DomainError(
            "Administrator authentication was cancelled",
            hint="nothing was changed — try again and approve the prompt",
            output=(result.stderr or result.stdout or "").strip(),
        )
    return result


# ───────────────────────────── discovery ─────────────────────────────

_KV_RE = re.compile(r"^\s*(?P<key>[a-z][a-z0-9-]*):\s*(?P<value>.*?)\s*$")


def parse_discover(text: str) -> Optional[RealmInfo]:
    """Parse ``realm discover`` output.

    Shape is a first line ``realm: NAME`` followed by indented ``key: value``
    lines. ``required-package`` repeats, so it becomes a list.
    """
    info: Optional[RealmInfo] = None
    packages: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("realm:"):
            name = stripped.split(":", 1)[1].strip()
            if name:
                info = RealmInfo(name=name)
            continue
        match = _KV_RE.match(line)
        if not match or info is None:
            continue
        key, value = match.group("key"), match.group("value")
        info.raw[key] = value
        if key == "required-package":
            packages.append(value)
        elif key == "configured":
            # In `discover` this is yes/no; in `list` it is the membership
            # mechanism ("kerberos-member"). Both mean "already joined" when
            # they are not "no".
            info.configured = value.strip().lower() not in ("", "no", "false")
        elif key == "server-software":
            info.server_software = value
        elif key == "client-software":
            info.client_software = value
        elif key == "domain-name":
            info.domain_name = value

    if info is not None:
        info.required_packages = packages
    return info


def parse_realm_list(text: str) -> list[RealmInfo]:
    """Parse ``realm list`` output — every joined realm, in order.

    Each realm starts with an unindented name; everything indented after it
    belongs to that realm. Skipping the indentation test would glue several
    realms together and report properties from the wrong one.
    """
    realms: list[RealmInfo] = []
    current: Optional[RealmInfo] = None

    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            current = RealmInfo(name=line.strip(), configured=True)
            realms.append(current)
            continue
        match = _KV_RE.match(line)
        if not match or current is None:
            continue
        key, value = match.group("key"), match.group("value")
        current.raw[key] = value
        if key == "domain-name":
            current.domain_name = value
        elif key == "server-software":
            current.server_software = value
        elif key == "client-software":
            current.client_software = value
        elif key == "configured":
            current.configured = value.strip().lower() not in ("", "no", "false")

    return realms



def discover(name: str, *, timeout: int = 60) -> Optional[RealmInfo]:
    """Ask DNS/realmd about a realm. Returns ``None`` if it is not reachable."""
    if not _have(REALM):
        raise DomainError(
            "realmd is not installed",
            hint="install it first: sudo dnf install realmd",
        )
    result = _run([REALM, "discover", "--verbose", name], timeout=timeout)
    if result.returncode != 0:
        return None
    return parse_discover(result.stdout)


def _realm_list_text() -> str:
    if not _have(REALM):
        return ""
    return _run([REALM, "list"], timeout=30).stdout


def realm_details() -> list[RealmInfo]:
    """Every joined realm with its membership details.

    ``realm list`` keys realms by the name they were joined under, which is not
    necessarily the realm name — joining the same domain twice with different
    casing produces two entries. Keeping the details lets the caller show which
    backend each one uses, which is the difference between a cosmetic duplicate
    and two identity providers fighting over the same domain.
    """
    return parse_realm_list(_realm_list_text())


def joined_realms() -> list[str]:
    """Names of the realms this machine is currently joined to."""
    return [realm.name for realm in realm_details()]


def login_policy() -> dict[str, str]:
    """The ``login-policy:`` style properties of the realm we are really joined to.

    Reads the first membership whose client is installed. Taking the first entry
    outright reports the policy of a leftover config file — on a machine with a
    stale winbind entry that means showing that realm's ``allow-any-login`` while
    the live sssd membership actually enforces ``allow-realm-logins``, i.e. the
    window would overstate who can log in.
    """
    live = [member for member in memberships() if member.live]
    if not live:
        return {}
    policy: dict[str, str] = {}
    for realm in realm_details():
        if realm.name != live[0].name:
            continue
        for key in ("login-policy", "configured", "domain-name"):
            if key in realm.raw:
                policy[key] = realm.raw[key]
        break
    return policy


# ───────────────────────────── readiness ─────────────────────────────


def _package_present(name: str) -> bool:
    if not _have(RPM):
        return True  # not an RPM distro; do not invent a failure
    return _run([RPM, "-q", name], timeout=30).returncode == 0


def missing_packages(names: tuple[str, ...] = REQUIRED_PACKAGES) -> list[str]:
    return [name for name in names if not _package_present(name)]


def _service_active(service: str) -> bool:
    if not _have(SYSTEMCTL):
        return False
    return _run([SYSTEMCTL, "is-active", "--quiet", service], timeout=30).returncode == 0


def authselect_profile() -> str:
    if not _have(AUTHSELECT):
        return ""
    result = _run([AUTHSELECT, "current"], timeout=30)
    if result.returncode != 0:
        return ""
    text = result.stdout.strip()
    # "Profile ID: sssd (with-mkhomedir feature enabled)" -> "sssd"
    match = re.search(r"Profile ID:\s*(?P<profile>[\w-]+)", text)
    return match.group("profile") if match else text.splitlines()[0] if text else ""


def prerequisites() -> list[Check]:
    """Everything bootstrap-system.sh guarantees, as inspectable checks."""
    checks: list[Check] = []

    missing = missing_packages()
    checks.append(
        Check(
            "AD packages",
            not missing,
            "all installed" if not missing else "missing: " + " ".join(missing),
        )
    )

    profile = authselect_profile()
    checks.append(
        Check(
            "authselect",
            profile.startswith("sssd"),
            f"profile {profile}" if profile else "authselect not configured",
        )
    )

    # oddjobd is what creates a home directory on first domain login. Without it
    # a domain user logs in to a read-only profile and everything looks broken.
    checks.append(Check("oddjobd", _service_active("oddjobd"), "home directory creation"))

    checks.append(
        Check("sssd", _service_active("sssd"), "identity provider", severity="warning")
    )

    for tool in ("realm", "adcli", "kinit"):
        checks.append(Check(tool, _have(tool), "command available"))

    if privilege_tool() is None:
        checks.append(
            Check(
                "administrator rights",
                False,
                "neither pkexec nor non-interactive sudo is available",
            )
        )
    else:
        checks.append(Check("administrator rights", True, "via " + str(privilege_tool())))

    return checks


def _unit_exists(unit: str) -> bool:
    """Whether a systemd unit file exists at all — installed, not necessarily active."""
    if not _have(SYSTEMCTL):
        return False
    return _run([SYSTEMCTL, "cat", unit], timeout=30).returncode == 0


def client_available(client: str) -> bool:
    """Is this identity client actually installed on this machine?

    Unknown clients return True: guessing "not installed" for something we do
    not recognise would invent a problem.
    """
    unit = CLIENT_UNITS.get(client)
    if unit is None:
        return True
    return _unit_exists(unit)


@dataclass
class Membership:
    """One line of ``realm list``, with whether its client really exists.

    ``realm list`` enumerates joined realms by reading the config file of each
    client — sssd.conf for sssd, smb.conf for winbind. A leftover file therefore
    produces an entry for a client that is not installed, which reads as a
    duplicate join but is not one.
    """

    name: str
    realm_name: str
    client: str
    live: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "realm_name": self.realm_name,
            "client": self.client,
            "live": self.live,
        }


def memberships(realms: Optional[list[RealmInfo]] = None) -> list[Membership]:
    """Every ``realm list`` entry, annotated with whether its client is installed."""
    if realms is None:
        realms = realm_details()
    return [
        Membership(
            name=realm.name,
            realm_name=realm.raw.get("realm-name", ""),
            client=realm.client_software or "unknown",
            live=client_available(realm.client_software),
        )
        for realm in realms
    ]


def duplicate_memberships(realms: Optional[list[RealmInfo]] = None) -> list[str]:
    """Realm names with more than one *installed* client.

    Only live memberships count. Two real clients on one domain means either can
    answer an identity lookup and each keeps its own copy of the machine account
    password, so which one wins depends on the PAM stack and a stale copy can
    break the trust relationship. An entry whose client is not installed is a
    leftover config file, not a conflict.
    """
    seen: dict[str, list[str]] = {}
    for member in memberships(realms):
        if not member.live:
            continue
        key = (member.realm_name or member.name).lower()
        seen.setdefault(key, []).append(member.name)
    return [", ".join(names) for names in seen.values() if len(names) > 1]


def stale_memberships(realms: Optional[list[RealmInfo]] = None) -> list[Membership]:
    """Entries realmd lists whose client is not installed.

    Harmless for logins, but they make ``realm list`` lie and can send
    ``realm leave`` after the wrong name.
    """
    return [member for member in memberships(realms) if not member.live]


def domain_checks(user: Optional[str] = None) -> list[Check]:
    """The post-join verification, mirroring check-domain.sh."""
    checks: list[Check] = []
    realms = joined_realms()
    checks.append(
        Check("domain join", bool(realms), ", ".join(realms) if realms else "not joined")
    )

    if not realms:
        return checks

    details = realm_details()
    if len(details) > 1:
        stale = stale_memberships(details)
        duplicates = duplicate_memberships(details)
        if stale:
            # Not a conflict: the client named in the entry is not installed, so
            # nothing is competing. Say what to look at instead of suggesting a
            # rejoin, which would be the wrong repair.
            names = ", ".join(f"{member.name} ({member.client})" for member in stale)
            checks.append(
                Check(
                    "memberships",
                    False,
                    f"{names} listed but {stale[0].client} is not installed "
                    "— leftover client config, not a second join. "
                    "`realm leave` may fail to remove it",
                    severity="warning",
                )
            )
        elif duplicates:
            # Genuinely two clients on one domain: both rotate the machine
            # account password, and a stale copy breaks the trust relationship.
            checks.append(
                Check(
                    "memberships",
                    False,
                    f"same domain joined twice: {'; '.join(duplicates)} "
                    "— leave one and rejoin",
                    severity="warning",
                )
            )
        else:
            checks.append(
                Check("memberships", True, f"{len(details)} memberships, one per domain")
            )

    checks.append(Check("sssd", _service_active("sssd"), "identity provider"))
    checks.append(Check("oddjobd", _service_active("oddjobd"), "home directory creation"))

    profile = authselect_profile()
    checks.append(
        Check("authselect", profile.startswith("sssd"), f"profile {profile}" or "unset")
    )

    policy = login_policy()
    checks.append(
        Check(
            "login policy",
            True,
            policy.get("login-policy", "unknown"),
            severity="warning",
        )
    )

    if user:
        resolved = _run(["id", user], timeout=30)
        checks.append(
            Check(
                "user resolution",
                resolved.returncode == 0,
                (resolved.stdout.strip() if resolved.returncode == 0 else f"{user} not found"),
            )
        )
        entry = _run(["getent", "passwd", user], timeout=30)
        checks.append(
            Check(
                "home directory",
                entry.returncode == 0 and bool(entry.stdout.strip()),
                entry.stdout.strip().splitlines()[0] if entry.stdout.strip() else "no passwd entry",
                severity="warning",
            )
        )

    hostname = _run(["hostname", "-f"], timeout=30)
    fqdn = hostname.stdout.strip()
    # A single-label hostname cannot be resolved by the DC, and the join
    # succeeds anyway — then nothing works and the cause is invisible.
    checks.append(
        Check(
            "fully qualified hostname",
            bool(fqdn) and "." in fqdn,
            fqdn or "unknown",
        )
    )
    return checks


def status(user: Optional[str] = None) -> dict:
    """One dict describing domain readiness and current membership."""
    realms = joined_realms()
    prereq = prerequisites()
    post = domain_checks(user) if realms else []
    all_checks = prereq + post
    blocking = [check for check in all_checks if not check.ok and check.severity == "required"]
    return {
        "joined": bool(realms),
        "realms": realms,
        "realms_detail": [
            {
                "name": realm.name,
                "realm_name": realm.raw.get("realm-name", ""),
                "domain_name": realm.domain_name,
                "client_software": realm.client_software,
                "login_formats": realm.raw.get("login-formats", ""),
                "login_policy": realm.raw.get("login-policy", ""),
                "client_installed": client_available(realm.client_software),
            }
            for realm in realm_details()
        ],
        "login_policy": login_policy(),
        "checks": [check.to_dict() for check in all_checks],
        "blocking": [check.name for check in blocking],
        "ready": not blocking,
        "privilege": privilege_tool() or "",
    }


# ───────────────────────────── join / leave ─────────────────────────────


def _admin_principal(user: str, realm_name: str) -> str:
    """Build the principal for the joining account.

    AD logins are case-insensitive but the realm is conventionally uppercase, and
    the scripts in use require an uppercase username. Accept whatever the user
    typed and normalise it here rather than rejecting it.
    """
    user = user.strip()
    if "@" in user:
        return user.upper().split("@")[0] + "@" + user.split("@", 1)[1].upper()
    return f"{user.upper()}@{realm_name.upper()}"


def join(
    realm_name: str,
    user: str,
    password: str,
    *,
    computer_ou: Optional[str] = None,
    computer_name: Optional[str] = None,
    permit_all: bool = True,
    timeout: int = 300,
) -> dict:
    """Join this machine to ``realm_name`` using ``user``.

    Returns the post-join status. Raises :class:`DomainError` with a hint on
    every failure path, because a failed join leaves the machine in a state the
    user cannot inspect without knowing where to look.
    """
    realm_name = realm_name.strip()
    if not realm_name:
        raise DomainError("No realm given", hint="pass the domain, e.g. EXAMPLE.COM")
    if not user.strip():
        raise DomainError("No joining account given", hint="an account with join rights is required")
    if not password:
        raise DomainError("No password given", hint="the joining account's password is required")

    if realm_name in joined_realms():
        raise DomainError(
            f"Already joined to {realm_name}",
            hint="leave the domain first if you want to rejoin",
        )

    if privilege_tool() is None:
        raise DomainError(
            "Cannot obtain administrator rights",
            hint="install polkit (pkexec), or configure sudo for your user",
        )

    missing = missing_packages()
    if missing:
        raise DomainError(
            "The domain packages are not installed",
            hint="install them first: " + install_command(missing),
        )

    # A FILE cache, because the privileged process runs as root: root cannot see
    # the user's KEYRING/KCM session cache, but it can read a file.
    admin = _admin_principal(user, realm_name)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    handle, cache_path = tempfile.mkstemp(prefix="krb5cc_krbctl_join.", dir=runtime_dir)
    os.close(handle)
    os.unlink(cache_path)  # kinit wants to create it itself
    cache = f"FILE:{cache_path}"

    try:
        kerberos.kinit_with_password(admin, password, cache, timeout=timeout)

        argv = [REALM, "join", "--user", user, "--no-password", "--unattended"]
        if computer_ou:
            argv.append(f"--computer-ou={computer_ou}")
        if computer_name:
            argv.append(f"--computer-name={computer_name}")
        argv.append(realm_name)

        result = _privileged(argv, env={"KRB5CCNAME": cache}, timeout=timeout)
        if result.returncode != 0:
            raise DomainError(
                f"Joining {realm_name} failed",
                hint="check that the account may join computers to the domain",
                output=_combined(result),
            )

        if permit_all:
            permit = _privileged([REALM, "permit", "--all"], timeout=timeout)
            # Not fatal: the machine is joined. Without it only listed users may
            # log in, which is a policy choice rather than a failure.
            if permit.returncode != 0:
                pass
    finally:
        # The throwaway ticket must not outlive the join.
        try:
            kerberos.destroy(cache)
        except kerberos.KerberosError:
            pass
        for leftover in (cache_path, cache_path + ".ccache"):
            try:
                os.unlink(leftover)
            except OSError:
                pass

    return status()


def leave(realm_name: str, *, user: Optional[str] = None, password: str = "", timeout: int = 300) -> dict:
    """Leave a realm. With ``user``+``password`` it authenticates first."""
    realm_name = realm_name.strip()
    if realm_name not in joined_realms():
        raise DomainError(
            f"Not joined to {realm_name}",
            hint="nothing to do",
        )

    argv = [REALM, "leave"]
    env: Optional[dict[str, str]] = None
    cache_path = ""

    if user and password:
        admin = _admin_principal(user, realm_name)
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        handle, cache_path = tempfile.mkstemp(prefix="krb5cc_krbctl_leave.", dir=runtime_dir)
        os.close(handle)
        os.unlink(cache_path)
        cache = f"FILE:{cache_path}"
        kerberos.kinit_with_password(admin, password, cache, timeout=timeout)
        env = {"KRB5CCNAME": cache}

    argv.append(realm_name)
    try:
        result = _privileged(argv, env=env, timeout=timeout)
        if result.returncode != 0:
            raise DomainError(
                f"Leaving {realm_name} failed",
                hint="the domain controller may be unreachable",
                output=_combined(result),
            )
    finally:
        if cache_path:
            try:
                kerberos.destroy(f"FILE:{cache_path}")
            except kerberos.KerberosError:
                pass
            try:
                os.unlink(cache_path)
            except OSError:
                pass

    return status()


def permit_all() -> None:
    """Allow any domain user to log in."""
    result = _privileged([REALM, "permit", "--all"])
    if result.returncode != 0:
        raise DomainError(
            "Could not change the login policy",
            hint="the machine may not be joined",
            output=_combined(result),
        )


def install_command(packages: list[str]) -> str:
    """The command to install prerequisites, for a hint the user can paste."""
    names = " ".join(packages) if packages else " ".join(REQUIRED_PACKAGES)
    if _have("dnf"):
        return f"sudo dnf install -y {names}"
    if _have("apt-get"):
        return f"sudo apt install -y {names}"
    return f"install: {names}"


def install_prerequisites(timeout: int = 900) -> dict:
    """Install the missing packages, via pkexec so it needs no terminal."""
    missing = missing_packages()
    if not missing:
        return {"installed": [], "already_present": True}

    manager = "dnf" if _have("dnf") else "apt-get" if _have("apt-get") else ""
    if not manager:
        raise DomainError(
            "No supported package manager found",
            hint=install_command(missing),
        )

    verb = ["install", "-y"] if manager == "dnf" else ["install", "-y"]
    result = _privileged([manager] + verb + missing, timeout=timeout)
    if result.returncode != 0:
        raise DomainError(
            "Installing the domain packages failed",
            hint="run the command by hand to see the full output",
            output=_combined(result),
        )
    return {"installed": missing, "already_present": False, "status": status()}


def _combined(result: subprocess.CompletedProcess) -> str:
    """Both streams, since realmd reports its real reason on either."""
    parts = [part.strip() for part in (result.stdout, result.stderr) if part and part.strip()]
    return "\n".join(parts)


def suggested_realm() -> str:
    """Guess the realm from the machine's current Kerberos configuration.

    A machine prepared by bootstrap-system.sh already has a ``default_realm`` in
    krb5.conf, so the join form can pre-fill it instead of making the user retype
    a name that is already configured.
    """
    try:
        return kerberos.default_realm()
    except Exception:
        return ""


__all__ = [
    "Check",
    "DomainError",
    "Membership",
    "client_available",
    "OPTIONAL_PACKAGES",
    "REQUIRED_PACKAGES",
    "RealmInfo",
    "discover",
    "domain_checks",
    "install_command",
    "install_prerequisites",
    "join",
    "joined_realms",
    "leave",
    "login_policy",
    "memberships",
    "missing_packages",
    "duplicate_memberships",
    "stale_memberships",
    "parse_discover",
    "parse_realm_list",
    "permit_all",
    "prerequisites",
    "privilege_tool",
    "realm_details",
    "status",
    "suggested_realm",
]
