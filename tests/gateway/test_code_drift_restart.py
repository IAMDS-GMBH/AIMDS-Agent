"""The gateway must reload itself after the checkout moves.

`hermes update` restarts every gateway, but the desktop updater
(`hermes-setup --update`) is a separate binary that does not — and restarting
the desktop app respawns the backend, not the gateway. The process therefore
kept serving pre-update code, with a log line as the only signal. Observed
symptom: MCP tools silently missing from every session, and a user restarting
the app to no effect.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


class _Runner:
    """Minimal stand-in exposing just what the watcher touches."""

    def __init__(self, shas, auto_restart=True):
        self._shas = list(shas)
        self._running = True
        self._auto = auto_restart
        self.restart_calls = []

    def _git_head_sha(self, repo_root):
        return self._shas.pop(0) if self._shas else None

    def _auto_restart_on_code_change(self):
        return self._auto

    def request_restart(self, *, detached=False, via_service=False):
        self.restart_calls.append({"detached": detached, "via_service": via_service})
        self._running = False  # end the watcher loop
        return True


async def _drive(runner, tmp_path):
    from gateway.run import GatewayRunner

    with patch("pathlib.Path.exists", return_value=True):
        await GatewayRunner._code_drift_watcher(runner, interval=0.01)


@pytest.mark.asyncio
async def test_restarts_detached_when_the_checkout_moved(tmp_path):
    runner = _Runner(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])

    await _drive(runner, tmp_path)

    assert runner.restart_calls == [{"detached": True, "via_service": False}]


@pytest.mark.asyncio
async def test_does_not_restart_while_the_commit_is_unchanged(tmp_path):
    runner = _Runner(["aaaaaaaaaaaa", "aaaaaaaaaaaa"])
    # Stop the loop after one poll so the test terminates.
    original = runner._git_head_sha

    def once(repo_root):
        value = original(repo_root)
        runner._running = False
        return value

    runner._git_head_sha = once

    await _drive(runner, tmp_path)

    assert runner.restart_calls == []


@pytest.mark.asyncio
async def test_opt_out_warns_instead_of_restarting(tmp_path):
    runner = _Runner(["aaaaaaaaaaaa", "bbbbbbbbbbbb"], auto_restart=False)

    def stop_after(repo_root):
        value = _Runner._git_head_sha(runner, repo_root)
        if not runner._shas:
            runner._running = False
        return value

    runner._git_head_sha = stop_after

    await _drive(runner, tmp_path)

    assert runner.restart_calls == []


class TestAutoRestartFlag:
    def _flag(self, cfg):
        from gateway.run import GatewayRunner

        with patch("hermes_cli.config.load_config", return_value=cfg):
            return GatewayRunner._auto_restart_on_code_change(MagicMock())

    def test_defaults_to_on(self):
        assert self._flag({}) is True

    def test_respects_explicit_false(self):
        assert self._flag({"gateway": {"auto_restart_on_code_change": False}}) is False

    def test_accepts_string_values(self):
        assert self._flag({"gateway": {"auto_restart_on_code_change": "false"}}) is False
        assert self._flag({"gateway": {"auto_restart_on_code_change": "true"}}) is True

    def test_unreadable_config_keeps_the_default(self):
        from gateway.run import GatewayRunner

        with patch("hermes_cli.config.load_config", side_effect=OSError("boom")):
            assert GatewayRunner._auto_restart_on_code_change(MagicMock()) is True
