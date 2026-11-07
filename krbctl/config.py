"""Account storage for krbctl.

One human-readable JSON file at ``~/.config/krbctl/accounts.json`` (mode 0600).
No passwords live here — password-method accounts keep their secret in the
Secret Service (GNOME Keyring / KWallet) under the same principal.

First run auto-seeds from ``~/.keytab`` so a working machine needs zero
configuration: if a keytab exists, its principal becomes an account with
auto-renew on.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import kerberos

APP_ID = "io.github.pan.Krbctl"
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = CONFIG_HOME / "krbctl"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"

METHOD_KEYTAB = "keytab"
METHOD_PASSWORD = "password"
METHODS = (METHOD_KEYTAB, METHOD_PASSWORD)

DEFAULT_POLL_SECONDS = 30
DEFAULT_WARN_MINUTES = 15
DEFAULT_KEYTAB = "~/.keytab"


@dataclass
class Account:
    """One Kerberos identity the app manages."""

    id: str
    principal: str
    label: str = ""
    method: str = METHOD_KEYTAB
    keytab: str = DEFAULT_KEYTAB
    cache: str = ""  # "" = whatever the session default cache is
    auto_renew: bool = True
    remember_password: bool = True
    warn_minutes: int = DEFAULT_WARN_MINUTES

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            self.method = METHOD_KEYTAB
        if not self.label:
            self.label = self.principal

    @property
    def short_principal(self) -> str:
        user, realm = kerberos.split_principal(self.principal)
        return f"{user}@{realm}" if realm else user

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Account":
        allowed = {f for f in cls.__dataclass_fields__}  # tolerate older files
        return cls(**{k: v for k, v in raw.items() if k in allowed})  # type: ignore[arg-type]


@dataclass
class Prefs:
    poll_seconds: int = DEFAULT_POLL_SECONDS
    notify: bool = True
    auto_renew_global: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Prefs":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (raw or {}).items() if k in allowed})  # type: ignore[arg-type]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "account"


@dataclass
class Store:
    """Load / save the account list, seeding it on first run."""

    path: Path = ACCOUNTS_FILE
    accounts: list[Account] = field(default_factory=list)
    prefs: Prefs = field(default_factory=Prefs)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self, *, seed: bool = True) -> "Store":
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            self.accounts = [
                Account.from_dict(item)
                for item in raw.get("accounts", [])
                if isinstance(item, dict) and item.get("principal")
            ]
            self.prefs = Prefs.from_dict(raw.get("prefs", {}))
        if seed and not self.accounts:
            if self.seed():
                self.save()
        return self

    def save(self) -> None:
        payload = {
            "version": 1,
            "accounts": [account.to_dict() for account in self.accounts],
            "prefs": self.prefs.to_dict(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a crash mid-write cannot truncate the file.
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".accounts-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=False)
                stream.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── account CRUD ──────────────────────────────────────────

    def get(self, account_id: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.id == account_id), None)

    def unique_id(self, principal: str) -> str:
        base = slugify(principal)
        candidate, counter = base, 2
        while self.get(candidate):
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def add(self, account: Account) -> Account:
        if not account.id:
            account.id = self.unique_id(account.principal)
        existing = self.get(account.id)
        if existing is not None:
            self.accounts[self.accounts.index(existing)] = account
        else:
            self.accounts.append(account)
        self.save()
        return account

    def remove(self, account_id: str) -> bool:
        account = self.get(account_id)
        if account is None:
            return False
        self.accounts.remove(account)
        self.save()
        return True

    # ── first-run detection ───────────────────────────────────

    def seed(self) -> bool:
        """Detect an existing keytab and turn it into an account.

        Zero-config on a machine that is already set up: no wizard, no typing a
        principal that the keytab itself already knows.
        """
        for candidate in (DEFAULT_KEYTAB, "~/.config/krb5.keytab", "/etc/krb5.keytab"):
            expanded = os.path.expanduser(candidate)
            if not os.path.exists(expanded):
                continue
            principals = kerberos.keytab_principals(expanded)
            if not principals:
                continue
            principal = principals[0]
            realm = kerberos.split_principal(principal)[1]
            self.accounts.append(
                Account(
                    id=slugify(principal),
                    principal=principal,
                    label=realm or principal,
                    method=METHOD_KEYTAB,
                    keytab=candidate,
                    auto_renew=True,
                )
            )
            return True
        return False
