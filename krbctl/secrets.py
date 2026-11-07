"""Password storage via the Secret Service (libsecret).

Passwords only ever reach this module; nothing sensitive is written by us.
If no keyring is running (a bare compositor session with no gnome-keyring or
kwalletd) every function degrades to a no-op and the caller falls back to
prompting the user each time.
"""

from __future__ import annotations

from typing import Optional

_SCHEMA_NAME = "io.github.pan.Krbctl"
_schema = None
_import_error: Optional[str] = None


def _get_schema():
    global _schema, _import_error
    if _schema is not None or _import_error is not None:
        return _schema
    try:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        _schema = Secret.Schema.new(
            _SCHEMA_NAME,
            Secret.SchemaFlags.NONE,
            {"principal": Secret.SchemaAttributeType.STRING},
        )
    except (ImportError, ValueError) as exc:  # no libsecret typelib
        _import_error = str(exc)
        _schema = None
    return _schema


def available() -> bool:
    """True when libsecret is importable (does not prove a keyring is running)."""
    return _get_schema() is not None


def _attrs(principal: str) -> dict:
    return {"principal": principal}


def store_password(principal: str, password: str, label: str = "") -> bool:
    schema = _get_schema()
    if schema is None or not password:
        return False
    try:
        from gi.repository import Secret

        return bool(
            Secret.password_store_sync(
                schema,
                _attrs(principal),
                Secret.COLLECTION_DEFAULT,
                label or f"Kerberos password for {principal}",
                password,
                None,
            )
        )
    except Exception:  # noqa: BLE001 — a missing keyring must never crash the app
        return False


def lookup_password(principal: str) -> Optional[str]:
    schema = _get_schema()
    if schema is None:
        return None
    try:
        from gi.repository import Secret

        return Secret.password_lookup_sync(schema, _attrs(principal), None) or None
    except Exception:  # noqa: BLE001
        return None


def forget_password(principal: str) -> None:
    schema = _get_schema()
    if schema is None:
        return
    try:
        from gi.repository import Secret

        Secret.password_clear_sync(schema, _attrs(principal), None)
    except Exception:  # noqa: BLE001
        pass
