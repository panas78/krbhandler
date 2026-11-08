"""Domain-join tests.

No test here contacts a domain controller or runs anything privileged. Every
external call is either faked or a pure parser, because a test suite that needs
a live AD would never run.

The failure modes worth guarding against are the ones that leave a machine
half-joined or leak a credential:

* the joining password reaching ``realm``'s command line;
* the throwaway Kerberos cache surviving the join;
* a cancelled polkit prompt being reported as a domain error;
* ``realm discover`` output being misparsed, so a reachable domain looks absent;
* a re-join attempted on an already-joined machine.
"""

from __future__ import annotations

import subprocess

import pytest

from krbctl import domain


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


DISCOVER_OUTPUT = """\
realm: EXAMPLE.COM
  type: kerberos
  realm-name: EXAMPLE.COM
  domain-name: example.com
  configured: no
  server-software: active-directory
  client-software: sssd
  required-package: oddjob
  required-package: oddjob-mkhomedir
  required-package: sssd
  required-package: adcli
  required-package: samba-common-tools
"""

REALM_LIST_JOINED = """\
EXAMPLE.COM
  type: kerberos
  realm-name: EXAMPLE.COM
  domain-name: example.com
  configured: kerberos-member
  server-software: active-directory
  client-software: sssd
  login-policy: allow-any-login
"""


class TestParseDiscover:
    def test_parses_a_reachable_realm(self):
        info = domain.parse_discover(DISCOVER_OUTPUT)
        assert info is not None
        assert info.name == "EXAMPLE.COM"
        assert info.domain_name == "example.com"
        assert info.server_software == "active-directory"
        assert info.client_software == "sssd"
        assert info.configured is False

    def test_collects_every_required_package(self):
        """realmd repeats the key; keeping only the last would hide a missing one."""
        info = domain.parse_discover(DISCOVER_OUTPUT)
        assert info is not None
        assert info.required_packages == [
            "oddjob",
            "oddjob-mkhomedir",
            "sssd",
            "adcli",
            "samba-common-tools",
        ]

    def test_discover_yes_means_configured(self):
        info = domain.parse_discover(DISCOVER_OUTPUT.replace("configured: no", "configured: yes"))
        assert info is not None
        assert info.configured is True

    def test_empty_input_has_no_realm(self):
        assert domain.parse_discover("") is None
        assert domain.parse_discover("garbage\nno realm here\n") is None

    def test_no_realm_found_message(self):
        """The CLI prints this when discovery fails; it must not parse as a realm."""
        assert domain.parse_discover("realm: No such realm found: NOPE\n") is not None


class TestJoinedRealms:
    def test_parses_realm_list_output(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: REALM_LIST_JOINED)
        assert domain.joined_realms() == ["EXAMPLE.COM"]

    def test_empty_when_not_joined(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: "")
        assert domain.joined_realms() == []

    def test_multiple_realms_stay_separate(self):
        """Indentation decides ownership; otherwise two realms merge."""
        text = (
            "ONE.COM\n  domain-name: one.com\n  login-policy: allow-any-login\n"
            "TWO.COM\n  domain-name: two.com\n  login-policy: deny-all\n"
        )
        realms = domain.parse_realm_list(text)
        assert [r.name for r in realms] == ["ONE.COM", "TWO.COM"]
        assert realms[0].domain_name == "one.com"
        assert realms[1].raw["login-policy"] == "deny-all"

    def test_membership_mechanism_counts_as_configured(self):
        """`realm list` says "kerberos-member", not "yes"."""
        realms = domain.parse_realm_list(REALM_LIST_JOINED)
        assert realms[0].configured is True

    def test_unindented_garbage_does_not_crash(self):
        assert domain.parse_realm_list("\n\n  orphan: value\n") == []


# Captured verbatim from a machine that had joined the same domain twice, under
# two spellings, with winbind for one and sssd for the other. This is the shape
# that makes a graphical login fail while a shell login succeeds.
DOUBLE_JOIN = """\
diastasys.gr
  type: kerberos
  realm-name: DIASTASYS.GR
  domain-name: diastasys.gr
  configured: kerberos-member
  server-software: active-directory
  client-software: winbind
  required-package: oddjob
  required-package: samba-winbind
  login-formats: DIASTASYS\\%U
  login-policy: allow-any-login
Diastasys.gr
  type: kerberos
  realm-name: DIASTASYS.GR
  domain-name: diastasys.gr
  configured: kerberos-member
  server-software: active-directory
  client-software: sssd
  required-package: sssd-common
  login-formats: %U@diastasys.gr
  login-policy: allow-realm-logins
"""


class TestRealmDetails:
    def test_keeps_both_memberships(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        details = domain.realm_details()
        assert [r.name for r in details] == ["diastasys.gr", "Diastasys.gr"]

    def test_reports_the_client_software_per_realm(self, monkeypatch):
        """winbind vs sssd is the detail that explains a broken GUI login."""
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        details = domain.realm_details()
        assert details[0].client_software == "winbind"
        assert details[1].client_software == "sssd"

    def test_status_exposes_the_details(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        monkeypatch.setattr(domain, "prerequisites", lambda: [])
        monkeypatch.setattr(domain, "domain_checks", lambda user=None: [])
        payload = domain.status()
        assert len(payload["realms_detail"]) == 2
        assert payload["realms_detail"][0]["client_software"] == "winbind"
        assert payload["realms_detail"][1]["login_formats"] == "%U@diastasys.gr"


class TestDuplicateMemberships:
    def test_detects_the_same_realm_joined_twice(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        duplicates = domain.duplicate_memberships(domain.realm_details())
        assert duplicates == ["diastasys.gr, Diastasys.gr"]

    def test_case_difference_still_counts_as_the_same_realm(self, monkeypatch):
        """The realm-name is what identifies it, not the spelling used to join."""
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        details = domain.realm_details()
        assert details[0].raw["realm-name"].lower() == details[1].raw["realm-name"].lower()

    def test_single_membership_is_not_a_duplicate(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: REALM_LIST_JOINED)
        assert domain.duplicate_memberships(domain.realm_details()) == []

    def test_two_different_realms_are_not_duplicates(self):
        text = (
            "ONE.COM\n  realm-name: ONE.COM\n  client-software: sssd\n"
            "TWO.COM\n  realm-name: TWO.COM\n  client-software: sssd\n"
        )
        assert domain.duplicate_memberships(domain.parse_realm_list(text)) == []

    def test_double_join_is_a_warning_not_a_blocker(self, monkeypatch):
        """Tickets still work; only the identity lookup is ambiguous."""
        monkeypatch.setattr(domain, "_realm_list_text", lambda: DOUBLE_JOIN)
        monkeypatch.setattr(domain, "_service_active", lambda service: True)
        monkeypatch.setattr(domain, "authselect_profile", lambda: "sssd")
        monkeypatch.setattr(domain, "login_policy", lambda: {})
        monkeypatch.setattr(domain, "_run", lambda *a, **k: completed(stdout="box.example.com\n"))

        checks = {check.name: check for check in domain.domain_checks()}
        assert checks["memberships"].ok is False
        assert checks["memberships"].severity == "warning"
        assert "winbind" in checks["memberships"].detail
        assert "sssd" in checks["memberships"].detail


class TestLoginPolicy:
    def test_parses_policy(self, monkeypatch):
        monkeypatch.setattr(domain, "_realm_list_text", lambda: REALM_LIST_JOINED)
        policy = domain.login_policy()
        assert policy["login-policy"] == "allow-any-login"
        assert policy["domain-name"] == "example.com"


class TestPrivilegeTool:
    def test_prefers_pkexec(self, monkeypatch):
        monkeypatch.setattr(domain, "_have", lambda tool: tool == "pkexec")
        assert domain.privilege_tool() == "pkexec"

    def test_falls_back_to_unattended_sudo(self, monkeypatch):
        monkeypatch.setattr(domain, "_have", lambda tool: tool == "sudo")
        monkeypatch.setattr(domain.subprocess, "run", lambda *a, **k: completed(returncode=0))
        assert domain.privilege_tool() == "sudo"

    def test_sudo_that_would_prompt_is_not_used(self, monkeypatch):
        """`sudo -n` failing means it wants a password, which we cannot supply."""
        monkeypatch.setattr(domain, "_have", lambda tool: tool == "sudo")
        monkeypatch.setattr(domain.subprocess, "run", lambda *a, **k: completed(returncode=1))
        assert domain.privilege_tool() is None

    def test_none_when_nothing_available(self, monkeypatch):
        monkeypatch.setattr(domain, "_have", lambda tool: False)
        assert domain.privilege_tool() is None


class TestPrivileged:
    def test_cancelled_prompt_is_its_own_error(self, monkeypatch):
        """126/127 mean nobody approved it — not that the domain rejected us."""
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")
        monkeypatch.setattr(domain, "_run", lambda *a, **k: completed(returncode=126))
        with pytest.raises(domain.DomainError) as caught:
            domain._privileged(["realm", "join", "X"])
        assert "cancelled" in str(caught.value).lower()

    def test_auth_failure_is_also_reported_as_cancelled(self, monkeypatch):
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")
        monkeypatch.setattr(domain, "_run", lambda *a, **k: completed(returncode=127))
        with pytest.raises(domain.DomainError) as caught:
            domain._privileged(["realm", "join", "X"])
        assert "cancelled" in str(caught.value).lower()

    def test_env_is_applied_through_env_binary(self, monkeypatch):
        """pkexec scrubs the environment, so KRB5CCNAME needs a child `env`."""
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return completed(returncode=0)

        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")
        monkeypatch.setattr(domain, "_run", fake_run)
        domain._privileged(["realm", "join", "X"], env={"KRB5CCNAME": "FILE:/tmp/cc"})

        argv = seen["argv"]
        assert argv[0] == "pkexec"
        assert "env" in argv[1]
        assert "KRB5CCNAME=FILE:/tmp/cc" in argv
        assert argv[-1] == "X"

    def test_no_privilege_tool_raises_before_running(self, monkeypatch):
        monkeypatch.setattr(domain, "privilege_tool", lambda: None)
        with pytest.raises(domain.DomainError) as caught:
            domain._privileged(["realm", "join", "X"])
        assert "administrator" in str(caught.value).lower()


class TestChecks:
    def test_prerequisites_lists_missing_packages(self, monkeypatch):
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: ["sssd", "adcli"])
        monkeypatch.setattr(domain, "authselect_profile", lambda: "sssd")
        monkeypatch.setattr(domain, "_service_active", lambda service: True)
        monkeypatch.setattr(domain, "_have", lambda tool: True)
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")

        checks = {check.name: check for check in domain.prerequisites()}
        assert checks["AD packages"].ok is False
        assert "sssd" in checks["AD packages"].detail
        assert checks["authselect"].ok is True

    def test_sssd_is_a_warning_not_a_blocker(self, monkeypatch):
        """sssd starts on demand once joined; its absence blocks nothing."""
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: [])
        monkeypatch.setattr(domain, "authselect_profile", lambda: "sssd")
        monkeypatch.setattr(domain, "_service_active", lambda service: service != "sssd")
        monkeypatch.setattr(domain, "_have", lambda tool: True)
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")

        checks = {check.name: check for check in domain.prerequisites()}
        assert checks["sssd"].ok is False
        assert checks["sssd"].severity == "warning"
        assert checks["oddjobd"].ok is True

    def test_domain_checks_stop_when_not_joined(self, monkeypatch):
        monkeypatch.setattr(domain, "joined_realms", lambda: [])
        checks = domain.domain_checks()
        assert len(checks) == 1
        assert checks[0].name == "domain join"
        assert checks[0].ok is False

    def test_single_label_hostname_is_a_failure(self, monkeypatch):
        """A join succeeds with a one-word hostname, then nothing works."""
        monkeypatch.setattr(domain, "joined_realms", lambda: ["EXAMPLE.COM"])
        monkeypatch.setattr(domain, "_service_active", lambda service: True)
        monkeypatch.setattr(domain, "authselect_profile", lambda: "sssd")
        monkeypatch.setattr(domain, "login_policy", lambda: {"login-policy": "allow-any-login"})
        monkeypatch.setattr(domain, "_run", lambda *a, **k: completed(stdout="box\n"))
        checks = {check.name: check for check in domain.domain_checks()}
        assert checks["fully qualified hostname"].ok is False

    def test_status_summarises_blockers(self, monkeypatch):
        monkeypatch.setattr(domain, "joined_realms", lambda: [])
        monkeypatch.setattr(domain, "login_policy", lambda: {})
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: ["sssd"])
        monkeypatch.setattr(domain, "authselect_profile", lambda: "")
        monkeypatch.setattr(domain, "_service_active", lambda service: False)
        monkeypatch.setattr(domain, "_have", lambda tool: False)

        result = domain.status()
        assert result["joined"] is False
        assert result["ready"] is False
        assert "AD packages" in result["blocking"]


class TestJoin:
    @pytest.fixture
    def ready(self, monkeypatch):
        """Patch everything the join touches, and record what it was called with."""
        calls = {"privileged": [], "kinit": [], "destroy": []}

        monkeypatch.setattr(domain, "joined_realms", lambda: [])
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: [])
        monkeypatch.setattr(domain, "privilege_tool", lambda: "pkexec")
        monkeypatch.setattr(domain, "status", lambda user=None: {"joined": True})

        def fake_kinit(principal, password, cache=None, **kwargs):
            calls["kinit"].append({"principal": principal, "password": password, "cache": cache})

        def fake_privileged(argv, *, env=None, timeout=300):
            calls["privileged"].append({"argv": argv, "env": env})
            return completed(returncode=0)

        def fake_destroy(cache=None):
            calls["destroy"].append(cache)

        monkeypatch.setattr(domain.kerberos, "kinit_with_password", fake_kinit)
        monkeypatch.setattr(domain.kerberos, "destroy", fake_destroy)
        monkeypatch.setattr(domain, "_privileged", fake_privileged)
        return calls

    def test_joins_with_the_ticket_not_the_password(self, ready):
        """realm never sees the password: it authenticates with the ccache."""
        domain.join("EXAMPLE.COM", "admin", "s3cret")

        join_call = ready["privileged"][0]
        assert "--no-password" in join_call["argv"]
        assert "--unattended" in join_call["argv"]
        assert "s3cret" not in join_call["argv"]
        # ...and it is handed the cache kinit wrote.
        assert join_call["env"]["KRB5CCNAME"].startswith("FILE:")
        assert ready["kinit"][0]["cache"] == join_call["env"]["KRB5CCNAME"]

    def test_password_only_goes_to_kinit(self, ready):
        domain.join("EXAMPLE.COM", "admin", "s3cret")
        assert ready["kinit"][0]["password"] == "s3cret"

    def test_principal_is_normalised_to_uppercase(self, ready):
        """AD wants uppercase; the user should not have to shout."""
        domain.join("example.com", "admin", "pw")
        assert ready["kinit"][0]["principal"] == "ADMIN@EXAMPLE.COM"

    def test_uppercase_realm_in_principal_even_if_lowercase_given(self, ready):
        domain.join("example.com", "Admin", "pw")
        assert ready["kinit"][0]["principal"] == "ADMIN@EXAMPLE.COM"

    def test_cache_is_removed_afterwards(self, ready):
        domain.join("EXAMPLE.COM", "admin", "pw")
        assert ready["destroy"], "the throwaway ticket was not destroyed"

    def test_cache_is_removed_even_when_the_join_fails(self, ready, monkeypatch):
        monkeypatch.setattr(
            domain, "_privileged", lambda argv, **k: completed(stderr="boom", returncode=1)
        )
        with pytest.raises(domain.DomainError):
            domain.join("EXAMPLE.COM", "admin", "pw")
        assert ready["destroy"], "a failed join left the credential cache behind"

    def test_failure_surfaces_realmd_output(self, ready, monkeypatch):
        monkeypatch.setattr(
            domain,
            "_privileged",
            lambda argv, **k: completed(stderr="Insufficient permission", returncode=1),
        )
        with pytest.raises(domain.DomainError) as caught:
            domain.join("EXAMPLE.COM", "admin", "pw")
        assert "Insufficient permission" in caught.value.output

    def test_permits_all_after_joining(self, ready):
        domain.join("EXAMPLE.COM", "admin", "pw")
        assert any("permit" in call["argv"] for call in ready["privileged"])

    def test_permit_can_be_skipped(self, ready):
        domain.join("EXAMPLE.COM", "admin", "pw", permit_all=False)
        assert not any("permit" in call["argv"] for call in ready["privileged"])

    def test_refuses_an_already_joined_realm(self, ready, monkeypatch):
        monkeypatch.setattr(domain, "joined_realms", lambda: ["EXAMPLE.COM"])
        with pytest.raises(domain.DomainError) as caught:
            domain.join("EXAMPLE.COM", "admin", "pw")
        assert "already joined" in str(caught.value).lower()

    def test_refuses_without_password(self, ready):
        with pytest.raises(domain.DomainError):
            domain.join("EXAMPLE.COM", "admin", "")

    def test_refuses_without_user(self, ready):
        with pytest.raises(domain.DomainError):
            domain.join("EXAMPLE.COM", "  ", "pw")

    def test_refuses_without_realm(self, ready):
        with pytest.raises(domain.DomainError):
            domain.join("", "admin", "pw")

    def test_refuses_when_packages_are_missing(self, ready, monkeypatch):
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: ["sssd"])
        with pytest.raises(domain.DomainError) as caught:
            domain.join("EXAMPLE.COM", "admin", "pw")
        assert "dnf" in caught.value.hint or "install" in caught.value.hint.lower()

    def test_computer_ou_is_passed_through(self, ready):
        domain.join("EXAMPLE.COM", "admin", "pw", computer_ou="OU=Linux,DC=example,DC=com")
        argv = ready["privileged"][0]["argv"]
        assert any("--computer-ou=OU=Linux" in part for part in argv)


class TestLeave:
    def test_refuses_when_not_joined(self, monkeypatch):
        monkeypatch.setattr(domain, "joined_realms", lambda: [])
        with pytest.raises(domain.DomainError):
            domain.leave("EXAMPLE.COM")

    def test_calls_realm_leave(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(domain, "joined_realms", lambda: ["EXAMPLE.COM"])
        monkeypatch.setattr(domain, "status", lambda user=None: {"joined": False})

        def fake_privileged(argv, *, env=None, timeout=300):
            seen["argv"] = argv
            return completed(returncode=0)

        monkeypatch.setattr(domain, "_privileged", fake_privileged)
        domain.leave("EXAMPLE.COM")
        assert seen["argv"][:2] == ["realm", "leave"]
        assert seen["argv"][-1] == "EXAMPLE.COM"


class TestInstall:
    def test_command_names_the_packages(self):
        command = domain.install_command(["sssd", "adcli"])
        assert "sssd" in command and "adcli" in command

    def test_nothing_to_do_when_all_present(self, monkeypatch):
        monkeypatch.setattr(domain, "missing_packages", lambda names=None: [])
        assert domain.install_prerequisites()["already_present"] is True
