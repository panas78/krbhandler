"""Autostart tests — the entry is a contract with the desktop environment.

The failure modes worth guarding against are all silent:

* an entry written but disabled by a flag we forgot to check, so login does
  nothing and the user has no idea why;
* ``is_enabled()`` trusting the file's existence, so a masked entry reports on;
* a re-enable that leaves duplicate keys or a stale ``Exec`` pointing at a
  launcher that has since moved;
* writing outside ``$XDG_CONFIG_HOME``, which puts the entry somewhere the
  desktop never reads.

Everything here is redirected into a tmp_path via ``XDG_CONFIG_HOME``, so the
real ``~/.config`` is never touched.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from krbctl import autostart


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the autostart directory at a scratch dir for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def launcher(tmp_path):
    """A fake installed launcher to point the entry at."""
    path = tmp_path / "bin" / "krbctl"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


class TestEntryLocation:
    def test_entry_lives_under_xdg_config_home(self, isolated_config):
        expected = isolated_config / "autostart" / autostart.ENTRY_NAME
        assert autostart.entry_path() == expected

    def test_falls_back_to_dot_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert autostart.entry_path() == tmp_path / ".config" / "autostart" / autostart.ENTRY_NAME

    def test_nothing_is_created_by_reading(self, isolated_config):
        """Reading status must not have the side effect of installing the hook."""
        autostart.status()
        assert not autostart.entry_path().exists()


class TestEnable:
    def test_enable_writes_a_valid_entry(self, launcher):
        path = autostart.enable(launcher)
        assert path.exists()

        entry = autostart.read_entry(path)
        assert entry["Type"] == "Application"
        assert entry["Exec"] == f"{launcher} tray"
        assert entry["TryExec"] == launcher
        assert entry["Icon"] == "krbctl"
        # A login hook, not something to launch by hand: the launcher entry is
        # io.github.pan.Krbctl.desktop.
        assert entry["NoDisplay"] == "true"
        assert entry["X-GNOME-Autostart-enabled"] == "true"
        # The name is generated, not copied from a file, so a rename has to
        # travel through APP_NAME or this entry keeps the old one forever.
        assert entry["Name"] == f"{autostart.APP_NAME} (tray)"

    def test_enable_reports_enabled(self, launcher):
        autostart.enable(launcher)
        assert autostart.is_enabled() is True

    def test_enable_is_idempotent(self, launcher):
        first = autostart.enable(launcher).read_text()
        second = autostart.enable(launcher).read_text()
        assert first == second

    def test_enable_replaces_a_masked_entry(self, launcher):
        """Switching back on must drop Hidden, not stack another key on it."""
        autostart.enable(launcher)
        autostart.disable()
        assert autostart.is_enabled() is False

        autostart.enable(launcher)
        assert autostart.is_enabled() is True
        entry = autostart.read_entry()
        assert "Hidden" not in entry or entry["Hidden"].lower() != "true"

    def test_enable_without_a_launcher_raises(self, monkeypatch):
        monkeypatch.setattr(autostart, "launcher", lambda: None)
        with pytest.raises(autostart.AutostartError):
            autostart.enable()

    def test_creates_the_autostart_directory(self, isolated_config, launcher):
        assert not (isolated_config / "autostart").exists()
        autostart.enable(launcher)
        assert (isolated_config / "autostart").is_dir()


class TestLauncherResolution:
    def test_finds_the_prefix_launcher_not_the_one_on_path(self, tmp_path, monkeypatch):
        """A custom-PREFIX install must point at its own binary.

        Regression: the prefix was located by counting parent directories, which
        silently produced a different krbctl off PATH instead of failing. An
        install into /tmp/whatever wrote an autostart entry for ~/.local/bin.
        """
        # Build a fake install layout: <prefix>/share/krbctl/krbctl/autostart.py
        prefix = tmp_path / "prefix"
        pkg = prefix / "share" / "krbctl" / "krbctl"
        pkg.mkdir(parents=True)
        prefix_launcher = prefix / "bin" / "krbctl"
        prefix_launcher.parent.mkdir(parents=True)
        prefix_launcher.write_text("#!/bin/sh\n")
        prefix_launcher.chmod(0o755)

        # Pretend this module lives there, and put a decoy on PATH.
        decoy_dir = tmp_path / "path"
        decoy_dir.mkdir()
        decoy = decoy_dir / "krbctl"
        decoy.write_text("#!/bin/sh\n")
        decoy.chmod(0o755)

        monkeypatch.setattr(autostart, "__file__", str(pkg / "autostart.py"))
        monkeypatch.setenv("PATH", str(decoy_dir))

        assert autostart.launcher() == str(prefix_launcher)

    def test_falls_back_to_path_without_a_prefix_launcher(self, tmp_path, monkeypatch):
        decoy_dir = tmp_path / "path"
        decoy_dir.mkdir()
        decoy = decoy_dir / "krbctl"
        decoy.write_text("#!/bin/sh\n")
        decoy.chmod(0o755)

        monkeypatch.setattr(autostart, "__file__", str(tmp_path / "nowhere" / "autostart.py"))
        monkeypatch.setenv("PATH", str(decoy_dir))

        assert autostart.launcher() == str(decoy)

    def test_returns_none_when_nothing_is_findable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(autostart, "__file__", str(tmp_path / "nowhere" / "autostart.py"))
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        assert autostart.launcher() is None


class TestDisable:
    def test_disable_masks_rather_than_deletes(self, launcher):
        """Deleting the user file would re-expose a system-wide entry."""
        autostart.enable(launcher)
        assert autostart.disable() is True

        path = autostart.entry_path()
        assert path.exists(), "entry was deleted; Hidden=true is the masking mechanism"
        assert autostart.read_entry(path)["Hidden"].lower() == "true"

    def test_disabled_entry_is_not_enabled(self, launcher):
        autostart.enable(launcher)
        autostart.disable()
        assert autostart.is_enabled() is False

    def test_disable_keeps_the_original_exec(self, launcher):
        """A disabled entry must still record what it would run."""
        autostart.enable(launcher)
        autostart.disable()
        assert autostart.read_entry()["Exec"] == f"{launcher} tray"

    def test_disable_when_absent_is_a_no_op(self):
        assert autostart.disable() is False
        assert not autostart.entry_path().exists()

    def test_gnome_flag_alone_counts_as_disabled(self, launcher):
        autostart.enable(launcher)
        path = autostart.entry_path()
        path.write_text(
            path.read_text().replace(
                "X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"
            )
        )
        assert autostart.is_enabled() is False


class TestStatus:
    def test_reports_enabled_and_a_live_binary(self, launcher):
        autostart.enable(launcher)
        info = autostart.status()
        assert info["enabled"] is True
        assert info["binary"] == launcher
        assert info["binary_exists"] is True
        assert info["path"] == str(autostart.entry_path())

    def test_reports_a_missing_binary(self, tmp_path, launcher):
        """An entry pointing at a moved launcher is a silent no-op at login."""
        autostart.enable(launcher)
        shutil.rmtree(tmp_path / "bin")
        info = autostart.status()
        assert info["enabled"] is True
        assert info["binary_exists"] is False

    def test_empty_when_nothing_installed(self):
        info = autostart.status()
        assert info["enabled"] is False
        assert info["exists"] is False


class TestShellIntegration:
    def test_entry_passes_desktop_file_validate(self, launcher):
        """A malformed entry is ignored by the desktop with no error at all."""
        if not shutil.which("desktop-file-validate"):
            pytest.skip("desktop-file-validate not installed")
        path = autostart.enable(launcher)
        result = subprocess.run(
            ["desktop-file-validate", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_masked_entry_also_validates(self, launcher):
        if not shutil.which("desktop-file-validate"):
            pytest.skip("desktop-file-validate not installed")
        autostart.enable(launcher)
        autostart.disable()
        result = subprocess.run(
            ["desktop-file-validate", str(autostart.entry_path())],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestCli:
    """The CLI is how install.sh drives this, so it has to work headless."""

    def test_on_off_round_trip(self, launcher, monkeypatch, capsys):
        from krbctl import cli

        monkeypatch.setattr(autostart, "launcher", lambda: launcher)

        assert cli.main(["autostart", "on"]) == cli.EXIT_OK
        assert autostart.is_enabled() is True
        assert "enabled" in capsys.readouterr().out

        assert cli.main(["autostart", "off"]) == cli.EXIT_OK
        assert autostart.is_enabled() is False
        assert "disabled" in capsys.readouterr().out

    def test_status_is_the_default_action(self, capsys):
        from krbctl import cli

        assert cli.main(["autostart"]) == cli.EXIT_OK
        assert "disabled" in capsys.readouterr().out

    def test_json_output_is_machine_readable(self, launcher, monkeypatch, capsys):
        import json

        from krbctl import cli

        monkeypatch.setattr(autostart, "launcher", lambda: launcher)
        cli.main(["autostart", "on"])
        capsys.readouterr()

        assert cli.main(["autostart", "status", "--json"]) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["enabled"] is True
        assert payload["binary_exists"] is True

    def test_on_fails_cleanly_without_a_launcher(self, monkeypatch, capsys):
        from krbctl import cli

        monkeypatch.setattr(autostart, "launcher", lambda: None)
        assert cli.main(["autostart", "on"]) == cli.EXIT_ERROR
        assert "install" in capsys.readouterr().err.lower()
