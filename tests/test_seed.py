"""Seeding an account from a ticket the login session already holds.

The bug this guards against was reported from a real machine: joined to the
domain, a valid TGT sitting in a ``KCM:`` cache, and the window showing "No
accounts" because seeding only ever looked for a keytab.

The trap in the fix is the cache name itself. ``KCM:1014801116:76132`` ends in
a login-session id, so an account that stores it reads an empty cache after the
next login. The seeded account must therefore keep ``cache`` empty and follow
whatever the session provides.
"""

from __future__ import annotations

from krbctl import config, kerberos


class TestSeedFromCache:
    def test_seeds_when_a_ticket_exists_but_no_keytab(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(
            kerberos,
            "list_caches",
            lambda: [("pperivolaris@DIASTASYS.GR", "KCM:1014801116:76132")],
        )

        store = config.Store(path=tmp_path / "accounts.json").load()
        assert len(store.accounts) == 1
        assert store.accounts[0].principal == "pperivolaris@DIASTASYS.GR"

    def test_seeded_account_follows_the_session_cache(self, monkeypatch, tmp_path):
        """Storing KCM:<uid>:<sid> would break at the next login."""
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@REALM", "KCM:1000:4242")])

        store = config.Store(path=tmp_path / "accounts.json")
        assert store.seed() is True
        assert store.accounts[0].cache == ""

    def test_seeded_account_is_marked_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@REALM", "KCM:1000:1")])

        store = config.Store(path=tmp_path / "accounts.json")
        store.seed()
        assert store.accounts[0].method == config.METHOD_SESSION

    def test_session_account_does_not_claim_auto_renew(self, monkeypatch, tmp_path):
        """No keytab and no password, so there is nothing to renew with."""
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@REALM", "KCM:1000:1")])

        store = config.Store(path=tmp_path / "accounts.json")
        store.seed()
        assert store.accounts[0].auto_renew is False

    def test_label_is_the_realm(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@DIASTASYS.GR", "KCM:1:1")])

        store = config.Store(path=tmp_path / "accounts.json")
        store.seed()
        assert store.accounts[0].label == "DIASTASYS.GR"

    def test_no_cache_no_account(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [])

        store = config.Store(path=tmp_path / "accounts.json")
        assert store.seed() is False
        assert store.accounts == []


class TestSeedPrecedence:
    def test_keytab_wins_over_a_session_ticket(self, monkeypatch, tmp_path):
        """A keytab can renew unattended; a session ticket cannot."""
        keytab_called = {"n": 0}

        def fake_keytab(self):
            keytab_called["n"] += 1
            self.accounts.append(
                config.Account(
                    id="me-realm",
                    principal="me@REALM",
                    method=config.METHOD_KEYTAB,
                    keytab="~/.keytab",
                )
            )
            return True

        monkeypatch.setattr(config.Store, "_seed_from_keytab", fake_keytab)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@REALM", "KCM:1:1")])

        store = config.Store(path=tmp_path / "accounts.json")
        assert store.seed() is True
        assert keytab_called["n"] == 1
        assert len(store.accounts) == 1
        assert store.accounts[0].method == config.METHOD_KEYTAB

    def test_seeding_is_persisted(self, monkeypatch, tmp_path):
        path = tmp_path / "accounts.json"
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("me@REALM", "KCM:1:1")])

        config.Store(path=path).load()
        assert path.exists()
        # A second load must not seed a duplicate.
        reloaded = config.Store(path=path).load()
        assert len(reloaded.accounts) == 1

    def test_empty_principal_is_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.Store, "_seed_from_keytab", lambda self: False)
        monkeypatch.setattr(kerberos, "list_caches", lambda: [("", "KCM:1:1")])

        store = config.Store(path=tmp_path / "accounts.json")
        assert store.seed() is False


class TestSessionMethod:
    def test_session_is_a_known_method(self):
        assert config.METHOD_SESSION in config.METHODS
        account = config.Account(id="x", principal="me@REALM", method=config.METHOD_SESSION)
        assert account.method == config.METHOD_SESSION

    def test_unknown_method_still_falls_back_to_keytab(self):
        account = config.Account(id="x", principal="me@REALM", method="nonsense")
        assert account.method == config.METHOD_KEYTAB
