"""Tests for the dev-checkout guard in ``hermes update``.

Background: ``hermes-update-autostash-*`` stashes accumulated silently in a
manually ``git clone``d development checkout (not the canonical managed
install at ``<HERMES_HOME>/hermes-agent``) because ``hermes update``'s
auto-stash-and-restore flow hard-resets the working tree on a restore
conflict without surfacing any error. This guard refuses to run that flow
non-interactively against a non-canonical checkout with a dirty tree, and
prints a loud warning when running interactively instead.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import config as hermes_config
from hermes_cli.main import cmd_update


def _run_side_effect(dirty_stdout=""):
    """Build a subprocess.run side_effect: dirty tree for status --porcelain,
    "already up to date" answers for the rest of the git plumbing so the
    flow can run to completion without crashing on a real branch/rev-list
    dance (mirrors _make_run_side_effect in test_cmd_update.py)."""

    def side_effect(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "status" in joined and "--porcelain" in joined:
            return SimpleNamespace(stdout=dirty_stdout, returncode=0)
        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return SimpleNamespace(stdout="main\n", returncode=0)
        if "rev-parse" in joined and "--verify" in joined:
            return SimpleNamespace(stdout="", returncode=0)
        if "rev-list" in joined:
            return SimpleNamespace(stdout="0\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0, stderr="")

    return side_effect


@pytest.fixture(autouse=True)
def _patch_managed_uv():
    """Match the rest of the update test suite: never touch real uv."""
    with patch("hermes_cli.managed_uv.resolve_uv", return_value=None), \
         patch("hermes_cli.managed_uv.ensure_uv", return_value=None), \
         patch("hermes_cli.managed_uv.update_managed_uv", return_value=None):
        yield


class TestIsCanonicalInstallLocation:
    def test_true_when_project_root_matches_managed_install(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        canonical = tmp_path / "hermes-agent"
        canonical.mkdir()

        assert hermes_config.is_canonical_install_location(canonical) is True

    def test_false_for_a_manually_cloned_dev_checkout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        dev_checkout = tmp_path / "dev-clones" / "hermes-agent"
        dev_checkout.mkdir(parents=True)

        assert hermes_config.is_canonical_install_location(dev_checkout) is False

    def test_false_when_path_resolution_fails(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "/nonexistent-hermes-home-for-test")

        assert (
            hermes_config.is_canonical_install_location(Path("/also/nonexistent"))
            is False
        )


@patch("hermes_cli.config.is_managed", return_value=False)
@patch("hermes_cli.config.detect_install_method", return_value="git")
@patch("hermes_cli.config.is_canonical_install_location", return_value=False)
class TestDevCheckoutGuard:
    def test_non_interactive_dirty_dev_checkout_refuses(
        self, _mock_canonical, _mock_method, _mock_managed, capsys
    ):
        """Desktop/gateway/--yes invocations must never silently stash+reset
        a dev checkout — this is exactly the scenario that produced the
        9 orphaned ``hermes-update-autostash-*`` stashes."""
        with patch("hermes_cli.main.subprocess.run") as mock_run:
            mock_run.side_effect = _run_side_effect(dirty_stdout=" M hermes_cli/main.py\n")

            with pytest.raises(SystemExit) as excinfo:
                cmd_update(SimpleNamespace(gateway=True))

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Development checkout detected" in out
        assert "uncommitted changes" in out

        # Must bail before ever attempting a stash or a pull.
        commands = [
            " ".join(str(a) for a in c.args[0])
            for c in mock_run.call_args_list
            if c.args
        ]
        assert not any("stash" in c for c in commands)
        assert not any("pull" in c for c in commands)

    def test_non_interactive_clean_dev_checkout_proceeds(
        self, _mock_canonical, _mock_method, _mock_managed, capsys
    ):
        """A clean tree in a dev checkout is not a risk — no warning, no exit."""
        with patch("hermes_cli.main.subprocess.run") as mock_run:
            mock_run.side_effect = _run_side_effect(dirty_stdout="")

            cmd_update(SimpleNamespace())

        out = capsys.readouterr().out
        assert "Development checkout detected" not in out

    def test_interactive_dirty_dev_checkout_warns_but_proceeds(
        self, _mock_canonical, _mock_method, _mock_managed, capsys, monkeypatch
    ):
        """A human at an interactive terminal sees the risk stated loudly,
        but is not blocked outright (matches existing interactive behavior
        for stash-and-ask elsewhere in this flow). The actual stash/restore
        dance is short-circuited here (mocked to a no-op) so the test only
        exercises the guard's own warning, not the unrelated interactive
        stash-restore prompt flow."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        with patch("hermes_cli.main.subprocess.run") as mock_run, \
             patch("hermes_cli.main._stash_local_changes_if_needed", return_value=None):
            mock_run.side_effect = _run_side_effect(dirty_stdout=" M hermes_cli/main.py\n")

            cmd_update(SimpleNamespace())

        out = capsys.readouterr().out
        assert "⚠ Development checkout detected" in out


@patch("hermes_cli.config.is_managed", return_value=False)
@patch("hermes_cli.config.detect_install_method", return_value="git")
@patch("hermes_cli.config.is_canonical_install_location", return_value=True)
def test_canonical_install_dirty_tree_is_unaffected_by_guard(
    _mock_canonical, _mock_method, _mock_managed, capsys
):
    """The managed install (what the Desktop app actually runs) must never
    be blocked by this guard, even non-interactively with a dirty tree —
    only non-canonical dev checkouts are refused."""
    with patch("hermes_cli.main.subprocess.run") as mock_run, \
         patch("hermes_cli.main._stash_local_changes_if_needed", return_value=None):
        mock_run.side_effect = _run_side_effect(dirty_stdout=" M hermes_cli/main.py\n")

        cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Development checkout detected" not in out
