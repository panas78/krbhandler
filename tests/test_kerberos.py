"""Unit tests for the pure parsing and classification logic.

These run without a KDC, without a keytab and without GTK — which is the point
of keeping :mod:`krbctl.kerberos` free of GUI imports.

The fixtures are C-locale ``klist`` output, because that is what the code
guarantees: every subprocess runs with ``LC_ALL=C``. Real output on a Greek
desktop reads ``07/11/2026`` for the 7th of November; under ``C`` the same
ticket prints ``11/07/2026``. Mixing the two silently shifts a ticket by
months, so the fixtures must stay C-locale.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from krbctl import kerberos

VALID_OUTPUT = """Ticket cache: KCM:1000
Default principal: pperivolaris@DIASTASYS.GR

Valid starting       Expires              Service principal
11/07/2026 15:02:49  11/08/2026 01:02:49  krbtgt/DIASTASYS.GR@DIASTASYS.GR
\trenew until 11/14/2026 15:02:49
11/07/2026 15:03:10  11/08/2026 01:02:49  HTTP/dc01.diastasys.gr@DIASTASYS.GR
"""

MULTI_LINE = """Ticket cache: FILE:/tmp/krb5cc_1000
Default principal: user@EXAMPLE.COM

Valid starting     Expires            Service principal
11/07/26 09:00:00  11/07/26 19:00:00  krbtgt/EXAMPLE.COM@EXAMPLE.COM
\t renew until 11/14/26 09:00:00
"""

NO_CACHE = "klist: No credentials cache found (filename: /tmp/krb5cc_1000)"


class TestParseKlist:
    def test_reads_principal_and_cache(self):
        ticket = kerberos.parse_klist(VALID_OUTPUT)
        assert ticket is not None
        assert ticket.principal == "pperivolaris@DIASTASYS.GR"
        assert ticket.cache == "KCM:1000"

    def test_takes_the_tgt_expiry_not_the_service_ticket(self):
        """The bug this guards against: reading the last row's timestamp."""
        ticket = kerberos.parse_klist(VALID_OUTPUT)
        assert ticket is not None
        assert ticket.expires == datetime(2026, 11, 8, 1, 2, 49)
        assert ticket.started == datetime(2026, 11, 7, 15, 2, 49)

    def test_reads_renew_until(self):
        ticket = kerberos.parse_klist(VALID_OUTPUT)
        assert ticket is not None
        assert ticket.renew_until == datetime(2026, 11, 14, 15, 2, 49)

    def test_service_list_excludes_the_renew_marker(self):
        ticket = kerberos.parse_klist(VALID_OUTPUT)
        assert ticket is not None
        assert all("renew" not in svc for svc in ticket.services)
        assert any(svc.startswith("HTTP/") for svc in ticket.services)

    def test_handles_two_digit_years(self):
        ticket = kerberos.parse_klist(MULTI_LINE)
        assert ticket is not None
        assert ticket.expires is not None
        assert ticket.expires.year == 2026
        assert ticket.renew_until is not None
        assert ticket.renew_until.year == 2026

    def test_missing_cache_returns_none(self):
        assert kerberos.parse_klist(NO_CACHE) is None

    def test_empty_output_returns_none(self):
        assert kerberos.parse_klist("") is None


class TestHumanize:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (None, "?"),
            (0, "expired"),
            (-60, "expired"),
            (45, "45s"),
            (600, "10m"),
            (3600, "1h"),
            (3540, "59m"),
            (34920, "9h42m"),
            (86400, "1d"),
            (176400, "2d01h"),
        ],
    )
    def test_formats(self, seconds, expected):
        assert kerberos.humanize(seconds) == expected


class TestStatus:
    """``status()`` is classification only — stub out the klist call."""

    @staticmethod
    def _ticket(delta: timedelta) -> kerberos.Ticket:
        return kerberos.Ticket(
            cache="KCM:1000",
            principal="user@EXAMPLE.COM",
            expires=datetime.now() + delta,
            services=["krbtgt/EXAMPLE.COM@EXAMPLE.COM"],
        )

    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(hours=9), "valid"),
            (timedelta(minutes=14), "expiring"),
            (timedelta(seconds=30), "expiring"),
            (timedelta(minutes=-1), "expired"),
            (timedelta(days=-3), "expired"),
        ],
    )
    def test_classification(self, monkeypatch, delta, expected):
        monkeypatch.setattr(kerberos, "read_ticket", lambda cache=None: self._ticket(delta))
        assert kerberos.status(warn_minutes=15).state == expected

    def test_no_ticket_is_none_state(self, monkeypatch):
        monkeypatch.setattr(kerberos, "read_ticket", lambda cache=None: None)
        assert kerberos.status().state == "none"

    def test_error_is_reported_not_raised(self, monkeypatch):
        def boom(cache=None):
            raise kerberos.KerberosError("cannot read", hint="klist failed")

        monkeypatch.setattr(kerberos, "read_ticket", boom)
        status = kerberos.status()
        assert status.state == "error"
        assert "klist failed" in status.message

    def test_warning_window_is_configurable(self, monkeypatch):
        monkeypatch.setattr(kerberos, "read_ticket", lambda cache=None: self._ticket(timedelta(minutes=20)))
        assert kerberos.status(warn_minutes=15).state == "valid"
        assert kerberos.status(warn_minutes=30).state == "expiring"

    def test_status_dataclass_labels(self):
        assert kerberos.Status(state="none").label == "no tgt"
        assert kerberos.Status(state="expired").label == "expired"
        assert kerberos.Status(state="error").label == "err"
        assert kerberos.Status(state="valid", seconds_left=34920).label == "9h42m"
        assert kerberos.Status(state="valid", seconds_left=34920).ok is True
        assert kerberos.Status(state="expired").ok is False


class TestSplitPrincipal:
    def test_splits(self):
        assert kerberos.split_principal("user@EXAMPLE.COM") == ("user", "EXAMPLE.COM")

    def test_uppercases_realm(self):
        assert kerberos.split_principal("user@example.com") == ("user", "EXAMPLE.COM")

    def test_without_realm(self):
        assert kerberos.split_principal("user") == ("user", "")


class TestFriendlyErrors:
    def test_hint_is_appended(self):
        error = kerberos.KerberosError("nope", hint="check the VPN")
        assert error.friendly == "nope. check the VPN"

    def test_no_hint(self):
        assert kerberos.KerberosError("nope").friendly == "nope"
