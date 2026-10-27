"""Tests for pre_approval_request / post_approval_response plugin hooks.

These hooks fire in tools/approval.py::check_all_command_guards whenever a
dangerous command needs user approval. They are observer-only (return values
ignored) and must fire on BOTH the CLI-interactive path and the async gateway
path, so external tools like macOS notifiers can be alerted regardless of
which surface the user is on.
"""
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools.approval import (
    check_all_command_guards,
    set_current_session_key,
    clear_session,
)


@pytest.fixture
def isolated_session(monkeypatch, tmp_path):
    """Give each test a fresh session_key, clean approval-state, and isolated
    HERMES_HOME so the real user's command_allowlist doesn't leak in."""
    import tools.approval as _am

    session_key = "test:session:approval_hooks"
    token = set_current_session_key(session_key)
    monkeypatch.setenv("HERMES_SESSION_KEY", session_key)
    # Make sure we don't skip guards via yolo / approvals.mode=off
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    # Isolate from the real user's permanent allowlist + session state
    _saved_permanent = _am._permanent_approved.copy()
    _saved_session = {k: v.copy() for k, v in _am._session_approved.items()}
    _am._permanent_approved.clear()
    _am._session_approved.clear()
    try:
        yield session_key
    finally:
        _am._permanent_approved.update(_saved_permanent)
        _am._session_approved.update(_saved_session)
        try:
            _am._approval_session_key.reset(token)
        except Exception:
            pass
        clear_session(session_key)


class TestCliPathFiresHooks:
    """CLI-interactive approval path: HERMES_INTERACTIVE is set, the
    prompt_dangerous_approval() result decides the outcome."""

    def test_pre_and_post_fire_with_expected_kwargs(
        self, isolated_session, monkeypatch
    ):
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        # approvals.mode=manual so we actually reach the prompt site
        monkeypatch.setattr(approval_module, "_get_approval_mode", lambda: "manual")

        captured = []

        def fake_invoke_hook(hook_name, **kwargs):
            captured.append((hook_name, kwargs))
            return []

        # Force the user to "approve once" via the approval_callback contract
        def cb(command, description, *, allow_permanent=True):
            return "once"

        with patch("hermes_cli.plugins.invoke_hook", side_effect=fake_invoke_hook):
            result = check_all_command_guards(
                "rm -rf /tmp/test-hook", "local", approval_callback=cb,
            )

        assert result["approved"] is True

        hook_names = [c[0] for c in captured]
        assert "pre_approval_request" in hook_names
        assert "post_approval_response" in hook_names

        pre_kwargs = next(kw for name, kw in captured if name == "pre_approval_request")
        assert pre_kwargs["command"] == "rm -rf /tmp/test-hook"
        assert pre_kwargs["surface"] == "cli"
        assert pre_kwargs["session_key"] == isolated_session
        assert isinstance(pre_kwargs["pattern_keys"], list)
        assert pre_kwargs["pattern_key"]  # non-empty primary pattern
        assert pre_kwargs["description"]

        post_kwargs = next(kw for name, kw in captured if name == "post_approval_response")
        assert post_kwargs["choice"] == "once"
        assert post_kwargs["surface"] == "cli"
        assert post_kwargs["command"] == "rm -rf /tmp/test-hook"

    def test_deny_reported_to_post_hook(self, isolated_session, monkeypatch):
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        monkeypatch.setattr(approval_module, "_get_approval_mode", lambda: "manual")

        captured = []

        def fake_invoke_hook(hook_name, **kwargs):
            captured.append((hook_name, kwargs))
            return []

        def cb(command, description, *, allow_permanent=True):
            return "deny"

        with patch("hermes_cli.plugins.invoke_hook", side_effect=fake_invoke_hook):
            result = check_all_command_guards(
                "rm -rf /tmp/test-deny", "local", approval_callback=cb,
            )

        assert result["approved"] is False
        post_kwargs = next(kw for name, kw in captured if name == "post_approval_response")
        assert post_kwargs["choice"] == "deny"

    def test_plugin_hook_crash_does_not_break_approval(
        self, isolated_session, monkeypatch
    ):
        """A crashing plugin must never prevent the approval flow from
        reaching the user. Hooks are observer-only and safety-critical
        behavior must be preserved."""
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        monkeypatch.setattr(approval_module, "_get_approval_mode", lambda: "manual")

        def boom(hook_name, **kwargs):
            raise RuntimeError("plugin crashed")

        def cb(command, description, *, allow_permanent=True):
            return "once"

        with patch("hermes_cli.plugins.invoke_hook", side_effect=boom):
            result = check_all_command_guards(
                "rm -rf /tmp/test-crash", "local", approval_callback=cb,
            )

        # User's approval was still honored despite the plugin crashing
        assert result["approved"] is True


class TestGatewayPathFiresHooks:
    """Async gateway approval path: HERMES_GATEWAY_SESSION is set and a
    gateway notify callback is registered. The agent thread blocks on the
    approval event until resolve_gateway_approval() is called from another
    thread."""




class TestGatewayWaitIsLogged:
    """AIS-746: a session blocked on an approval must be visible in the logs —
    session 20261027_170015_2d955b waited 5 minutes without a single line."""

    def _decide(self, monkeypatch, caplog, *, timeout, answer=None):
        monkeypatch.setattr(approval_module, "_get_approval_config", lambda: {"gateway_timeout": timeout})
        session_key = "test:session:approval_log"

        def notify(_data):
            if answer:
                approval_module.resolve_gateway_approval(session_key, answer)

        data = {"command": "rm -f /tmp/b13.txt", "description": "delete in root path", "pattern_key": "delete in root path"}
        with caplog.at_level("INFO", logger="tools.approval"):
            return approval_module._await_gateway_decision(session_key, notify, data)

    def test_request_and_timeout_are_logged(self, monkeypatch, caplog):
        result = self._decide(monkeypatch, caplog, timeout=0)

        assert result["resolved"] is False
        messages = [r.getMessage() for r in caplog.records]
        assert any("approval requested: session=test:session:approval_log" in m and "rm -f /tmp/b13.txt" in m for m in messages)
        assert any(m.startswith("approval timed out: session=test:session:approval_log") for m in messages)

    def test_answer_is_logged(self, monkeypatch, caplog):
        result = self._decide(monkeypatch, caplog, timeout=5, answer="deny")

        assert result == {"resolved": True, "choice": "deny"}
        assert any(r.getMessage().startswith("approval answered (deny):") for r in caplog.records)
