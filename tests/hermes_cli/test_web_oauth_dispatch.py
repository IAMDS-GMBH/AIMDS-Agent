"""Regression tests for the OAuth dispatcher in hermes_cli.web_server.

Bug history (2026-05-09): the `_OAUTH_PROVIDER_CATALOG` had two entries
flagged ``flow: "pkce"`` — anthropic and minimax-oauth — and the
dispatcher ``start_oauth_login`` hardcoded ``_start_anthropic_pkce()``
for any pkce-flagged provider. So clicking "Login" next to MiniMax in
the dashboard's Keys tab silently launched the Anthropic/Claude OAuth
flow.

The fix:
  1. Catalog entry for minimax-oauth changed from ``flow: "pkce"`` to
     ``flow: "device_code"`` (the actual UX is verification URI + user
     code + background poll, with PKCE as a security extension).
  2. New MiniMax branch added to ``_start_device_code_flow``.
  3. Dispatcher tightened: pkce branch now requires
     ``provider_id == "anthropic"``, so any future PKCE provider added
     without an explicit branch gets a clean ``400 Unsupported flow``
     instead of silently launching Anthropic OAuth.

These tests pin the corrected behavior.
"""
import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from hermes_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}


def _fake_nous_device_data():
    return {
        "device_code": "device-code",
        "user_code": "NOUS-1234",
        "verification_uri": "https://portal.nousresearch.com/device",
        "verification_uri_complete": (
            "https://portal.nousresearch.com/device?user_code=NOUS-1234"
        ),
        "expires_in": 600,
        "interval": 5,
    }


def _invoke_scope_refusal():
    request = httpx.Request("POST", "https://portal.nousresearch.com/oauth/device/code")
    response = httpx.Response(
        400,
        json={
            "error": "invalid_scope",
            "error_description": "unsupported scope inference:invoke",
        },
        request=request,
    )
    return httpx.HTTPStatusError("invalid scope", request=request, response=response)


def test_minimax_login_does_not_launch_anthropic_flow():
    """Click 'Login' on MiniMax → MUST NOT return claude.ai auth_url."""
    fake_user_code_resp = {
        "user_code": "ABCD-1234",
        "verification_uri": "https://api.minimax.io/oauth/verify",
        # `expired_in` < 1e12 so the heuristic treats it as seconds.
        "expired_in": 600,
        "interval": 2000,
        "state": "stub-state",
    }
    with patch(
        "hermes_cli.auth._minimax_request_user_code",
        return_value=fake_user_code_resp,
    ), patch(
        "hermes_cli.auth._minimax_pkce_pair",
        return_value=("verifier-stub", "challenge-stub", "stub-state"),
    ), patch(
        "hermes_cli.web_server._minimax_poller",
        return_value=None,
    ):
        resp = client.post(
            "/api/providers/oauth/minimax-oauth/start",
            headers=HEADERS,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The bug used to return Anthropic's auth_url — make sure the response
    # references neither the auth_url field nor anything Claude-related.
    assert "auth_url" not in body
    assert "claude.ai" not in str(body).lower()

    # And the response IS the device-code shape pointing at MiniMax.
    assert body["flow"] == "device_code"
    assert "minimax" in body["verification_url"].lower()
    assert body["user_code"] == "ABCD-1234"
    assert body["expires_in"] == 600


def test_nous_dashboard_device_flow_ignores_legacy_scope_override(monkeypatch):
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    requested_scopes = []

    def fake_request_device_code(**kwargs):
        requested_scopes.append(kwargs["scope"])
        return _fake_nous_device_data()

    monkeypatch.setenv("HERMES_AGENT_USE_LEGACY_SESSION_KEYS", "true")
    monkeypatch.setattr(auth_mod, "_request_device_code", fake_request_device_code)
    monkeypatch.setattr(ws, "_nous_poller", lambda sid: None)

    result = asyncio.run(ws._start_device_code_flow("nous"))
    try:
        assert requested_scopes == [auth_mod.DEFAULT_NOUS_SCOPE]
        assert result["flow"] == "device_code"
        assert result["user_code"] == "NOUS-1234"
        assert (
            ws._oauth_sessions[result["session_id"]]["scope"]
            == auth_mod.DEFAULT_NOUS_SCOPE
        )
    finally:
        ws._oauth_sessions.pop(result["session_id"], None)


def test_nous_dashboard_device_flow_does_not_retry_legacy_scope_on_invoke_refusal(monkeypatch):
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    requested_scopes = []

    def fake_request_device_code(**kwargs):
        requested_scopes.append(kwargs["scope"])
        raise _invoke_scope_refusal()

    monkeypatch.delenv("HERMES_AGENT_USE_LEGACY_SESSION_KEYS", raising=False)
    monkeypatch.setattr(auth_mod, "_request_device_code", fake_request_device_code)
    monkeypatch.setattr(ws, "_nous_poller", lambda sid: None)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(ws._start_device_code_flow("nous"))
    assert requested_scopes == [auth_mod.DEFAULT_NOUS_SCOPE]


def test_codex_dashboard_worker_persists_runtime_provider(tmp_path, monkeypatch):
    from hermes_cli import web_server as ws
    from hermes_cli.auth import get_active_provider
    from hermes_cli.runtime_provider import resolve_runtime_provider

    access_token = "h.eyJleHAiOjk5OTk5OTk5OTl9.s"

    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            if url.endswith("/deviceauth/usercode"):
                return _Resp(200, {
                    "device_auth_id": "device-auth-id",
                    "interval": 3,
                    "user_code": "CODEX-1234",
                })
            if url.endswith("/deviceauth/token"):
                return _Resp(200, {
                    "authorization_code": "authorization-code",
                    "code_verifier": "code-verifier",
                })
            return _Resp(200, {
                "access_token": access_token,
                "refresh_token": "codex-refresh",
            })

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(ws.time, "sleep", lambda _: None)

    sid, _ = ws._new_oauth_session("openai-codex", "device_code")
    try:
        ws._codex_full_login_worker(sid)

        assert ws._oauth_sessions[sid]["status"] == "approved"
        assert get_active_provider() == "openai-codex"

        runtime = resolve_runtime_provider(requested=None)
        assert runtime["provider"] == "openai-codex"
        assert runtime["api_key"] == access_token
        assert runtime["api_mode"] == "codex_responses"
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_nous_dashboard_poller_preserves_effective_scope_when_token_omits_scope(monkeypatch):
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    session_id = "nous-effective-scope-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "nous",
        "flow": "device_code",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "portal_base_url": "https://portal.nousresearch.com",
        "client_id": "hermes-cli",
        "device_code": "device-code",
        "interval": 5,
        "expires_at": time.time() + 600,
        "scope": auth_mod.DEFAULT_NOUS_SCOPE,
    }
    captured_state = {}

    def fake_refresh_nous_oauth_from_state(state, **kwargs):
        captured_state.update(state)
        return {**state, "agent_key": "jwt-agent-key"}

    monkeypatch.setattr(
        auth_mod,
        "_poll_for_token",
        lambda **kwargs: {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "refresh_nous_oauth_from_state",
        fake_refresh_nous_oauth_from_state,
    )
    monkeypatch.setattr(auth_mod, "persist_nous_credentials", lambda state: None)

    try:
        ws._nous_poller(session_id)
        assert captured_state["scope"] == auth_mod.DEFAULT_NOUS_SCOPE
        assert ws._oauth_sessions[session_id]["status"] == "approved"
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_minimax_dashboard_poller_accepts_absolute_ms_expired_in():
    """Dashboard MiniMax completion must accept unix-ms token expiry values."""
    from hermes_cli import web_server as ws

    now = datetime.now(timezone.utc)
    abs_ms = int((now.timestamp() + 1800) * 1000)
    session_id = "minimax-absolute-ms-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "minimax-oauth",
        "flow": "device_code",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "portal_base_url": "https://api.minimax.io",
        "client_id": "client-id",
        "user_code": "ABCD-1234",
        "code_verifier": "verifier",
        "interval_ms": 2000,
        "expired_in_raw": abs_ms,
        "region": "global",
    }
    captured_state = {}

    try:
        with patch(
            "hermes_cli.auth._minimax_poll_token",
            return_value={
                "status": "success",
                "access_token": "access",
                "refresh_token": "refresh",
                "expired_in": abs_ms,
                "token_type": "Bearer",
            },
        ), patch(
            "hermes_cli.auth._minimax_save_auth_state",
            side_effect=lambda state: captured_state.update(state),
        ):
            ws._minimax_poller(session_id)
    finally:
        ws._oauth_sessions.pop(session_id, None)

    assert captured_state["access_token"] == "access"
    assert 1790 <= captured_state["expires_in"] <= 1810
    assert datetime.fromisoformat(captured_state["expires_at"]).year < 9999


def test_anthropic_pkce_branch_still_works():
    """Sanity: the dispatcher tightening doesn't break the legitimate Anthropic PKCE path."""
    fake_anthropic_response = {
        "session_id": "stub-session",
        "flow": "pkce",
        "auth_url": "https://claude.ai/oauth/authorize?code=true&...",
        "expires_in": 600,
    }
    with patch(
        "hermes_cli.web_server._start_anthropic_pkce",
        return_value=fake_anthropic_response,
    ):
        resp = client.post(
            "/api/providers/oauth/anthropic/start",
            headers=HEADERS,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["flow"] == "pkce"
    assert "claude.ai" in body["auth_url"]


def test_xai_oauth_listed_as_loopback_flow(monkeypatch):
    """xAI Grok OAuth must surface in the catalog as a first-class loopback flow.

    Since AIS-325 ``/api/providers/oauth`` lists every non-hidden catalog
    entry regardless of login state; the logged-in stub only keeps this test
    independent of the developer machine's real xAI credentials.
    """
    from hermes_cli import web_server as ws

    real_status = ws._resolve_provider_status

    def _status(provider_id, status_fn):
        if provider_id == "xai-oauth":
            return {"logged_in": True, "source": "test", "source_label": "test",
                    "token_preview": "…abcd", "expires_at": None, "has_refresh_token": True}
        return real_status(provider_id, status_fn)

    monkeypatch.setattr(ws, "_resolve_provider_status", _status)
    resp = client.get("/api/providers/oauth", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    providers = {p["id"]: p for p in resp.json()["providers"]}
    assert "xai-oauth" in providers
    assert providers["xai-oauth"]["flow"] == "loopback"
    assert "grok" in providers["xai-oauth"]["name"].lower()


def test_xai_loopback_start_returns_authorize_url(monkeypatch):
    """Start MUST bind the loopback listener and hand back an xAI authorize URL."""
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    class _FakeServer:
        def shutdown(self):
            pass

        def server_close(self):
            pass

    class _FakeThread:
        def join(self, timeout=None):
            pass

    redirect_uri = (
        f"http://{auth_mod.XAI_OAUTH_REDIRECT_HOST}:{auth_mod.XAI_OAUTH_REDIRECT_PORT}"
        f"{auth_mod.XAI_OAUTH_REDIRECT_PATH}"
    )

    monkeypatch.setattr(
        auth_mod,
        "_xai_oauth_discovery",
        lambda *a, **k: {
            "authorization_endpoint": "https://auth.x.ai/oauth2/auth",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "_xai_start_callback_server",
        lambda *a, **k: (_FakeServer(), _FakeThread(), {"code": None, "error": None}, redirect_uri),
    )
    # Don't let the background worker run a real callback wait/exchange.
    monkeypatch.setattr(ws, "_xai_loopback_worker", lambda sid: None)

    resp = client.post("/api/providers/oauth/xai-oauth/start", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    try:
        assert body["flow"] == "loopback"
        assert "user_code" not in body  # loopback has nothing to paste/show
        assert body["auth_url"].startswith("https://auth.x.ai/oauth2/auth?")
        assert "code_challenge" in body["auth_url"]
        sess = ws._oauth_sessions[body["session_id"]]
        assert sess["provider"] == "xai-oauth"
        assert sess["flow"] == "loopback"
    finally:
        ws._oauth_sessions.pop(body["session_id"], None)


def test_xai_loopback_worker_persists_tokens_on_success(monkeypatch):
    """The worker exchanges the callback code and marks the session approved."""
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    saved = {}
    session_id = "xai-loopback-success-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "xai-oauth",
        "flow": "loopback",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "server": object(),
        "thread": object(),
        "callback_result": {"code": "auth-code", "state": "st"},
        "redirect_uri": "http://127.0.0.1:56121/callback",
        "verifier": "verifier",
        "challenge": "challenge",
        "state": "st",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
    }

    monkeypatch.setattr(
        auth_mod,
        "_xai_wait_for_callback",
        lambda *a, **k: {"code": "auth-code", "state": "st"},
    )
    monkeypatch.setattr(
        auth_mod,
        "_xai_oauth_exchange_code_for_tokens",
        lambda **k: {
            "access_token": "xai-access",
            "refresh_token": "xai-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "_save_xai_oauth_tokens",
        lambda tokens, **k: saved.update(tokens),
    )
    monkeypatch.setattr(ws, "_add_xai_oauth_pool_entry", lambda *a, **k: None)

    try:
        ws._xai_loopback_worker(session_id)
        assert ws._oauth_sessions[session_id]["status"] == "approved"
        assert saved["access_token"] == "xai-access"
        assert saved["refresh_token"] == "xai-refresh"
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_xai_loopback_worker_fails_on_state_mismatch(monkeypatch):
    """A mismatched OAuth state must fail the session, not persist tokens."""
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    session_id = "xai-loopback-state-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "xai-oauth",
        "flow": "loopback",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "server": object(),
        "thread": object(),
        "callback_result": {},
        "redirect_uri": "http://127.0.0.1:56121/callback",
        "verifier": "verifier",
        "challenge": "challenge",
        "state": "expected-state",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "discovery": {},
    }

    monkeypatch.setattr(
        auth_mod,
        "_xai_wait_for_callback",
        lambda *a, **k: {"code": "auth-code", "state": "ATTACKER-state"},
    )

    def _boom(**kwargs):
        raise AssertionError("token exchange must not run on state mismatch")

    monkeypatch.setattr(auth_mod, "_xai_oauth_exchange_code_for_tokens", _boom)

    try:
        ws._xai_loopback_worker(session_id)
        sess = ws._oauth_sessions[session_id]
        assert sess["status"] == "error"
        assert "state mismatch" in sess["error_message"].lower()
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_xai_loopback_worker_skips_persist_when_cancelled(monkeypatch):
    """If the session is cancelled while waiting, the worker must not persist."""
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    session_id = "xai-loopback-cancel-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "xai-oauth",
        "flow": "loopback",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "server": object(),
        "thread": object(),
        "callback_result": {},
        "redirect_uri": "http://127.0.0.1:56121/callback",
        "verifier": "verifier",
        "challenge": "challenge",
        "state": "st",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "discovery": {},
    }

    def _wait_then_cancel(*args, **kwargs):
        # Simulate the user cancelling (DELETE /sessions/{id}) while we were
        # blocked on the callback: the session vanishes, then a valid code
        # arrives. The worker must notice and bail before persisting.
        ws._oauth_sessions.pop(session_id, None)
        return {"code": "auth-code", "state": "st"}

    monkeypatch.setattr(auth_mod, "_xai_wait_for_callback", _wait_then_cancel)

    def _must_not_persist(*args, **kwargs):
        raise AssertionError("tokens must not be persisted for a cancelled session")

    monkeypatch.setattr(auth_mod, "_save_xai_oauth_tokens", _must_not_persist)
    monkeypatch.setattr(ws, "_add_xai_oauth_pool_entry", _must_not_persist)

    # Should return cleanly without raising and without persisting.
    ws._xai_loopback_worker(session_id)
    assert session_id not in ws._oauth_sessions


def test_cancel_loopback_session_shuts_down_callback_server():
    """Cancelling a loopback session must free the bound callback port now."""
    from hermes_cli import web_server as ws

    shutdown_calls = {"shutdown": 0, "close": 0, "join": 0}

    class _FakeServer:
        def shutdown(self):
            shutdown_calls["shutdown"] += 1

        def server_close(self):
            shutdown_calls["close"] += 1

    class _FakeThread:
        def join(self, timeout=None):
            shutdown_calls["join"] += 1

    # callback_result is the dict the worker's _xai_wait_for_callback polls.
    callback_result = {"code": None, "error": None}
    session_id = "xai-loopback-cancel-shutdown-test"
    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "xai-oauth",
        "flow": "loopback",
        "created_at": time.time(),
        "status": "pending",
        "server": _FakeServer(),
        "thread": _FakeThread(),
        "callback_result": callback_result,
    }

    try:
        resp = client.delete(
            f"/api/providers/oauth/sessions/{session_id}", headers=HEADERS
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert shutdown_calls == {"shutdown": 1, "close": 1, "join": 1}
        # The waiting worker must be signalled so it returns promptly instead
        # of spinning until the timeout.
        assert callback_result["error"] == "cancelled"
        assert session_id not in ws._oauth_sessions
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_unknown_pkce_provider_rejected_cleanly():
    """A future PKCE provider without an explicit branch must NOT silently route to Anthropic.

    Simulates a hypothetical catalog entry with ``flow: "pkce"`` and an
    id other than "anthropic". The dispatcher should fall through past
    the pkce branch (now gated on provider_id) and the device_code
    branch, then hit "Unsupported flow" — proving the bug class is
    structurally prevented.
    """
    from hermes_cli import web_server as ws

    # Inject a hypothetical catalog entry that's pkce-flagged but isn't
    # anthropic. This shape mirrors what would happen if a developer
    # added a new provider entry without remembering to wire up its
    # start function.
    fake_entry = {
        "id": "hypothetical-pkce-provider",
        "name": "Hypothetical PKCE Provider",
        "flow": "pkce",
        "cli_command": "hermes auth add hypothetical-pkce-provider",
        "docs_url": "https://example.com",
        "status_fn": None,
    }
    original_catalog = ws._OAUTH_PROVIDER_CATALOG
    try:
        ws._OAUTH_PROVIDER_CATALOG = original_catalog + (fake_entry,)
        resp = client.post(
            "/api/providers/oauth/hypothetical-pkce-provider/start",
            headers=HEADERS,
        )
    finally:
        ws._OAUTH_PROVIDER_CATALOG = original_catalog

    # Either 400 "Unsupported flow" (the explicit fall-through) or any
    # 4xx — what we MUST NOT see is a 200 with claude.ai in the body.
    assert resp.status_code >= 400, resp.text
    assert "claude.ai" not in resp.text.lower()


# ---------------------------------------------------------------------------
# Microsoft / M365 device-code flow
# ---------------------------------------------------------------------------
#
# Mirrors hermes_cli/mcp_catalog.py's `_microsoft_device_code_login` (the
# CLI equivalent used by `hermes mcp install MSOffice365MCP`) but surfaces
# the user_code/verification_url through the dashboard's generic device-code
# session mechanism instead of blocking a CLI prompt. MSAL's
# `acquire_token_by_device_flow` blocks internally while it polls, so the
# whole call is driven by `_microsoft_device_code_worker` in a background
# thread -- the dashboard's poll endpoint just reads `sess["status"]`.


class _FakeMsalApp:
    """Stand-in for msal.PublicClientApplication used across these tests."""

    def __init__(self, *args, **kwargs):
        pass

    def initiate_device_flow(self, scopes=None):
        return {
            "user_code": "MSFT-1234",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        }

    def acquire_token_by_device_flow(self, flow):
        return {"access_token": "fake-msal-dashboard-token"}


def test_microsoft_dashboard_device_flow_returns_device_code_shape(monkeypatch):
    """Starting the microsoft flow must return the same shape as github/nous
    (session_id, flow: device_code, user_code, verification_url, ...) so the
    existing dashboard UI needs zero changes to render it."""
    import msal
    from hermes_cli import web_server as ws

    monkeypatch.setattr(msal, "PublicClientApplication", _FakeMsalApp)
    monkeypatch.setattr(ws, "_microsoft_device_code_worker", lambda *a, **kw: None)

    result = asyncio.run(ws._start_device_code_flow("microsoft"))
    try:
        assert result["flow"] == "device_code"
        assert result["user_code"] == "MSFT-1234"
        assert result["verification_url"] == "https://microsoft.com/devicelogin"
        assert result["expires_in"] == 900
        assert result["poll_interval"] == 5
        assert ws._oauth_sessions[result["session_id"]]["provider"] == "microsoft"
        assert ws._oauth_sessions[result["session_id"]]["status"] == "pending"
    finally:
        ws._oauth_sessions.pop(result["session_id"], None)


def test_microsoft_dashboard_device_flow_rejects_incomplete_response(monkeypatch):
    """If MSAL's initiate_device_flow() doesn't return a user_code (e.g. bad
    client id), the endpoint must surface a clean error, not crash."""
    import msal
    from hermes_cli import web_server as ws

    class _BadFlowApp(_FakeMsalApp):
        def initiate_device_flow(self, scopes=None):
            return {"error": "invalid_client", "error_description": "bad client id"}

    monkeypatch.setattr(msal, "PublicClientApplication", _BadFlowApp)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(ws._start_device_code_flow("microsoft"))
    assert "bad client id" in str(excinfo.value) or "500" in str(excinfo.value)


def test_microsoft_device_code_worker_saves_token(monkeypatch, tmp_path):
    """On success the worker must save the token to M365_ACCESS_TOKEN and
    mark the session approved -- mirroring the CLI's device-code handler."""
    from hermes_cli import web_server as ws
    from hermes_cli.config import get_env_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        ws._microsoft_device_code_worker(sid, _FakeMsalApp(), {"user_code": "MSFT-1234"})

        assert ws._oauth_sessions[sid]["status"] == "approved"
        assert get_env_value("M365_ACCESS_TOKEN") == "fake-msal-dashboard-token"
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_microsoft_device_code_worker_no_token_marks_error(monkeypatch, tmp_path):
    """A device-code flow that never completes (declined/expired) must mark
    the session as errored, not crash and not save a token."""
    from hermes_cli import web_server as ws
    from hermes_cli.config import get_env_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _DeclinedApp(_FakeMsalApp):
        def acquire_token_by_device_flow(self, flow):
            return {"error": "authorization_declined"}

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        ws._microsoft_device_code_worker(sid, _DeclinedApp(), {"user_code": "MSFT-1234"})

        assert ws._oauth_sessions[sid]["status"] == "error"
        assert get_env_value("M365_ACCESS_TOKEN") is None
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_microsoft_listed_as_device_code_flow_in_catalog():
    from hermes_cli import web_server as ws

    entry = next(p for p in ws._OAUTH_PROVIDER_CATALOG if p["id"] == "microsoft")
    assert entry["flow"] == "device_code"


def test_microsoft_dashboard_device_flow_requests_manifest_scopes(monkeypatch):
    """The dashboard-initiated Microsoft device-code login must request the
    MSOffice365MCP catalog manifest's declared scopes (Mail/Calendar/Teams/
    etc.), not just "User.Read" -- a User.Read-only login is insufficient
    for any of the MCP's actual tools to work."""
    import msal
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    requested_scopes = {}

    class _ScopeCapturingApp(_FakeMsalApp):
        def initiate_device_flow(self, scopes=None):
            requested_scopes["scopes"] = scopes
            return super().initiate_device_flow(scopes=scopes)

    monkeypatch.setattr(msal, "PublicClientApplication", _ScopeCapturingApp)
    monkeypatch.setattr(ws, "_microsoft_device_code_worker", lambda *a, **kw: None)

    entry = mcp_catalog.get_entry("MSOffice365MCP")
    assert entry is not None and entry.auth.scopes, (
        "MSOffice365MCP manifest must declare auth.scopes for this test to "
        "be meaningful"
    )

    result = asyncio.run(ws._start_device_code_flow("microsoft"))
    try:
        assert requested_scopes["scopes"] == entry.auth.scopes
        assert "User.Read" in requested_scopes["scopes"]
        assert requested_scopes["scopes"] != ["User.Read"]
    finally:
        ws._oauth_sessions.pop(result["session_id"], None)


# --------------------------------------------------------------------------- AIS-286 consent tiers

def test_microsoft_device_code_worker_consent_error_carries_action_url(monkeypatch, tmp_path):
    """"Need admin approval" must surface as a structured consent error with
    the tenant-admin consent URL so the UI can offer it as an action."""
    from hermes_cli import web_server as ws
    from hermes_cli.config import get_env_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("M365_TENANT_ID", raising=False)

    class _ConsentApp(_FakeMsalApp):
        def acquire_token_by_device_flow(self, flow):
            return {"error": "invalid_grant", "error_description": "AADSTS90094: The grant requires admin permission."}

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        ws._microsoft_device_code_worker(sid, _ConsentApp(), {"user_code": "MSFT-1234"})
        sess = ws._oauth_sessions[sid]
        assert sess["status"] == "error"
        assert sess["error_code"] == "AADSTS90094"
        assert sess["error_category"] == "consent"
        assert "v2.0/adminconsent" in sess["action_url"]
        assert get_env_value("M365_ACCESS_TOKEN") is None

        poll = asyncio.run(ws.poll_oauth_session("microsoft", sid))
        assert poll["error_code"] == "AADSTS90094"
        assert poll["action_url"] == sess["action_url"]
        assert "admin" in poll["error_message"].lower()
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_microsoft_device_code_worker_declined_has_no_action_url(monkeypatch, tmp_path):
    from hermes_cli import web_server as ws

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _DeclinedApp(_FakeMsalApp):
        def acquire_token_by_device_flow(self, flow):
            return {"error": "authorization_declined"}

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        ws._microsoft_device_code_worker(sid, _DeclinedApp(), {"user_code": "MSFT-1234"})
        poll = asyncio.run(ws.poll_oauth_session("microsoft", sid))
        assert poll["error_category"] == "declined"
        assert "action_url" not in poll
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_microsoft_dashboard_login_requests_self_consent_tier(monkeypatch):
    """The dashboard button requests exactly the self-consent tier (AIS-286)."""
    import msal
    from hermes_cli import web_server as ws
    from hermes_cli.m365_auth import M365_LOGIN_SCOPES, M365_SELF_CONSENT_SCOPES

    requested = {}

    class _ScopeApp(_FakeMsalApp):
        def initiate_device_flow(self, scopes=None):
            requested["scopes"] = scopes
            return super().initiate_device_flow(scopes=scopes)

    monkeypatch.setattr(msal, "PublicClientApplication", _ScopeApp)
    monkeypatch.setattr(ws, "_microsoft_device_code_worker", lambda *a, **kw: None)
    result = asyncio.run(ws._start_device_code_flow("microsoft"))
    try:
        assert requested["scopes"] == M365_LOGIN_SCOPES == M365_SELF_CONSENT_SCOPES
        assert "Chat.ReadWrite" not in requested["scopes"]
    finally:
        ws._oauth_sessions.pop(result["session_id"], None)


def test_microsoft_admin_consent_url_endpoint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from hermes_cli import web_server as ws

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("M365_CLIENT_ID", "M365_TENANT_ID"):
        monkeypatch.delenv(var, raising=False)

    class _App:
        def get_accounts(self):
            return [{"home_account_id": "a"}]

        def acquire_token_silent(self, scopes, account=None):
            from hermes_cli.m365_auth import M365_STANDARD_SCOPES

            return {"access_token": "t"} if scopes == M365_STANDARD_SCOPES else None

    monkeypatch.setattr("hermes_cli.m365_auth.get_msal_app", lambda *a, **kw: _App())

    client = TestClient(ws.app)
    assert client.get("/api/providers/oauth/microsoft/admin-consent-url").status_code in (401, 403)
    resp = client.get(
        "/api/providers/oauth/microsoft/admin-consent-url",
        headers={"X-Hermes-Session-Token": ws._SESSION_TOKEN},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["url"].startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?client_id=41c29967")
    assert data["granted_tier"] == "standard"
    assert data["org_consented"] is True
    assert "Chat.ReadWrite" in data["org_consent_scopes"]
    assert "Chat.ReadWrite" not in data["self_consent_scopes"]


# ---------------------------------------------------------------------------
# AIS-304: a Microsoft sign-in must never re-install a live MSOffice365MCP
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid=777, running=False):
        self.pid = pid
        self._running = running

    def poll(self):
        return None if self._running else 0


def test_auto_enable_installed_enables_and_reconnects_without_spawning(monkeypatch):
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    monkeypatch.setattr(mcp_catalog, "is_installed", lambda name: True)
    ensure_calls = []
    monkeypatch.setattr(
        mcp_catalog, "ensure_m365_toolset_enabled", lambda: ensure_calls.append(1) or (True, None)
    )
    reconnects = []
    monkeypatch.setattr("tools.mcp_tool.reconnect_mcp_server", lambda name: reconnects.append(name) or [])

    def _no_spawn(*a, **kw):
        raise AssertionError("must not spawn an install for an installed entry")

    monkeypatch.setattr(ws, "_spawn_hermes_action", _no_spawn)

    assert ws._auto_enable_m365_toolset() == (True, None)
    assert ensure_calls == [1]
    assert reconnects == ["MSOffice365MCP"]


def test_auto_enable_installed_and_enabled_does_not_reconnect(monkeypatch):
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    monkeypatch.setattr(mcp_catalog, "is_installed", lambda name: True)
    monkeypatch.setattr(mcp_catalog, "ensure_m365_toolset_enabled", lambda: (False, None))

    def _no_reconnect(name):
        raise AssertionError("nothing changed -> no reconnect")

    monkeypatch.setattr("tools.mcp_tool.reconnect_mcp_server", _no_reconnect)
    assert ws._auto_enable_m365_toolset() == (False, None)


def test_auto_enable_not_installed_spawns_background_install(monkeypatch):
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    monkeypatch.setattr(mcp_catalog, "is_installed", lambda name: False)

    def _inline(*a, **kw):
        raise AssertionError("inline install_entry must not run on the request path")

    monkeypatch.setattr(mcp_catalog, "install_entry", _inline)
    ws._ACTION_PROCS.pop("mcp-install", None)
    spawned = []
    monkeypatch.setattr(ws, "_spawn_hermes_action", lambda sub, name: spawned.append((sub, name)) or _FakeProc())

    assert ws._auto_enable_m365_toolset() == (True, None)
    assert spawned == [(["mcp", "install", "MSOffice365MCP"], "mcp-install")]


def test_auto_enable_does_not_start_a_second_install_action(monkeypatch):
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    monkeypatch.setattr(mcp_catalog, "is_installed", lambda name: False)
    ws._ACTION_PROCS["mcp-install"] = _FakeProc(running=True)
    try:
        def _no_spawn(*a, **kw):
            raise AssertionError("install action already running")

        monkeypatch.setattr(ws, "_spawn_hermes_action", _no_spawn)
        assert ws._auto_enable_m365_toolset() == (False, None)
    finally:
        ws._ACTION_PROCS.pop("mcp-install", None)


def test_microsoft_device_code_worker_toolset_error_keeps_login_approved(monkeypatch, tmp_path, caplog):
    """The enable step must neither block nor hide: login stays approved and
    the error is logged (it used to be swallowed silently)."""
    import logging

    from hermes_cli import web_server as ws
    from hermes_cli.config import get_env_value

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ws, "_auto_enable_m365_toolset", lambda: (False, "boom"))

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        with caplog.at_level(logging.WARNING, logger=ws._log.name):
            ws._microsoft_device_code_worker(sid, _FakeMsalApp(), {"user_code": "MSFT-1234"})
        assert ws._oauth_sessions[sid]["status"] == "approved"
        assert get_env_value("M365_ACCESS_TOKEN") == "fake-msal-dashboard-token"
        assert any("boom" in rec.getMessage() for rec in caplog.records)
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_microsoft_device_code_worker_never_reinstalls_installed_mcp(monkeypatch, tmp_path):
    from hermes_cli import mcp_catalog
    from hermes_cli import web_server as ws

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(mcp_catalog, "is_installed", lambda name: True)
    monkeypatch.setattr(mcp_catalog, "ensure_m365_toolset_enabled", lambda: (False, None))

    def _no_install(*a, **kw):
        raise AssertionError("install must not run after login for an installed entry")

    monkeypatch.setattr(mcp_catalog, "install_entry", _no_install)
    monkeypatch.setattr(mcp_catalog, "_do_git_install", _no_install)
    monkeypatch.setattr(ws, "_spawn_hermes_action", _no_install)

    sid, _ = ws._new_oauth_session("microsoft", "device_code")
    try:
        ws._microsoft_device_code_worker(sid, _FakeMsalApp(), {"user_code": "MSFT-1234"})
        assert ws._oauth_sessions[sid]["status"] == "approved"
    finally:
        ws._oauth_sessions.pop(sid, None)


def test_listing_includes_unconnected_providers_and_hides_hidden_entries(monkeypatch):
    """AIS-325: the settings page and the onboarding overlay need every
    connectable provider, not just the ones already logged in."""
    from hermes_cli import web_server as ws

    monkeypatch.setattr(
        ws, "_resolve_provider_status", lambda provider_id, status_fn: {"logged_in": False, "source": None}
    )
    resp = client.get("/api/providers/oauth", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    providers = {p["id"]: p for p in resp.json()["providers"]}

    for expected in ("nous", "openai-codex", "anthropic", "claude-code"):
        assert expected in providers, expected
        assert providers[expected]["status"]["logged_in"] is False
    assert "iamds-keycloak" not in providers  # hidden=True stays out
    assert providers["claude-code"]["name"] == "Claude Code (OAuth)"
    assert providers["anthropic"]["name"] == "Anthropic (OAuth)"
    # Catalog order is preserved for the UI.
    ids = [p["id"] for p in resp.json()["providers"]]
    assert ids == [p["id"] for p in ws._OAUTH_PROVIDER_CATALOG if not p.get("hidden")]


# ---------------------------------------------------------------------------
# Google Gemini loopback flow (AIS-325)
# ---------------------------------------------------------------------------


def test_gemini_listed_as_loopback_flow_without_credentials(monkeypatch):
    from hermes_cli import auth as auth_mod

    monkeypatch.setattr(
        auth_mod, "get_gemini_oauth_auth_status", lambda: {"logged_in": False, "error": "not logged in"}
    )
    resp = client.get("/api/providers/oauth", headers=HEADERS)
    providers = {p["id"]: p for p in resp.json()["providers"]}
    gem = providers["google-gemini-cli"]
    assert gem["flow"] == "loopback"
    assert gem["name"] == "Google Gemini (OAuth)"
    assert gem["status"]["logged_in"] is False


def test_gemini_status_adapter_never_exposes_the_access_token(monkeypatch):
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    monkeypatch.setattr(
        auth_mod,
        "get_gemini_oauth_auth_status",
        lambda: {
            "logged_in": True,
            "auth_file": "/tmp/google_oauth.json",
            "source": "google-oauth",
            "api_key": "ya29.super-secret-access-token-value",
            "expires_at_ms": 1_800_000_000_000,
            "email": "user@example.com",
            "project_id": "p",
        },
    )
    status = ws._gemini_oauth_status()
    assert status["logged_in"] is True
    assert status["source_label"] == "user@example.com"
    assert status["expires_at"].endswith("Z")
    assert "api_key" not in status
    assert "super-secret" not in repr(status)


def test_gemini_loopback_start_returns_google_authorize_url(monkeypatch):
    from agent import google_oauth
    from hermes_cli import web_server as ws

    class _Login:
        auth_url = "https://accounts.google.com/o/oauth2/v2/auth?code_challenge=x&state=s"
        state = "s"

    monkeypatch.setattr(google_oauth, "begin_browser_flow", lambda **k: _Login())
    monkeypatch.setattr(ws, "_gemini_loopback_worker", lambda sid: None)

    resp = client.post("/api/providers/oauth/google-gemini-cli/start", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    try:
        assert body["flow"] == "loopback"
        assert body["auth_url"].startswith("https://accounts.google.com/")
        assert "code_challenge" in body["auth_url"]
        sess = ws._oauth_sessions[body["session_id"]]
        assert sess["provider"] == "google-gemini-cli"
        assert isinstance(sess["login"], _Login)
    finally:
        ws._oauth_sessions.pop(body["session_id"], None)


def test_gemini_loopback_start_surfaces_missing_client_id(monkeypatch):
    from agent import google_oauth

    def _boom(**k):
        raise google_oauth.GoogleOAuthError(
            "Google OAuth client ID is not available. Install gemini-cli.",
            code="google_oauth_client_id_missing",
        )

    monkeypatch.setattr(google_oauth, "begin_browser_flow", _boom)
    resp = client.post("/api/providers/oauth/google-gemini-cli/start", headers=HEADERS)
    assert resp.status_code == 400
    assert "gemini-cli" in resp.json()["detail"]


def _gemini_session(ws, session_id):
    class _Login:
        auth_url = "https://accounts.google.com/x"
        ready = None

    ws._oauth_sessions[session_id] = {
        "session_id": session_id,
        "provider": "google-gemini-cli",
        "flow": "loopback",
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
        "login": _Login(),
    }


def test_gemini_loopback_worker_marks_active_and_mirrors_pool_entry(monkeypatch):
    from agent import google_oauth
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    session_id = "gemini-loopback-success-test"
    _gemini_session(ws, session_id)
    marked, pooled = {}, {}
    monkeypatch.setattr(
        google_oauth,
        "finish_browser_flow",
        lambda login, *, timeout: google_oauth.GoogleCredentials(
            access_token="g-access", refresh_token="g-refresh", expires_ms=123,
            email="user@example.com", project_id="proj",
        ),
    )
    monkeypatch.setattr(auth_mod, "_mark_google_gemini_cli_active", lambda creds: marked.update(creds))
    monkeypatch.setattr(ws, "_add_gemini_oauth_pool_entry", lambda creds: pooled.update(creds))

    try:
        ws._gemini_loopback_worker(session_id)
        sess = ws._oauth_sessions[session_id]
        assert sess["status"] == "approved"
        assert "login" not in sess
        assert marked["email"] == "user@example.com" and marked["access_token"] == "g-access"
        assert pooled["refresh_token"] == "g-refresh"
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_gemini_loopback_worker_reports_sign_in_failure(monkeypatch):
    from agent import google_oauth
    from hermes_cli import auth as auth_mod
    from hermes_cli import web_server as ws

    session_id = "gemini-loopback-failure-test"
    _gemini_session(ws, session_id)

    def _fail(login, *, timeout):
        raise google_oauth.GoogleOAuthError("Authorization failed: access_denied")

    monkeypatch.setattr(google_oauth, "finish_browser_flow", _fail)
    monkeypatch.setattr(google_oauth, "abort_browser_flow", lambda login: None)
    monkeypatch.setattr(
        auth_mod, "_mark_google_gemini_cli_active", lambda creds: pytest.fail("must not persist")
    )
    try:
        ws._gemini_loopback_worker(session_id)
        sess = ws._oauth_sessions[session_id]
        assert sess["status"] == "error"
        assert "access_denied" in sess["error_message"]
    finally:
        ws._oauth_sessions.pop(session_id, None)


def test_gemini_cancel_releases_listener(monkeypatch):
    import threading

    from agent import google_oauth
    from hermes_cli import web_server as ws

    session_id = "gemini-loopback-cancel-test"
    _gemini_session(ws, session_id)
    login = ws._oauth_sessions[session_id]["login"]
    login.ready = threading.Event()
    aborted = []
    monkeypatch.setattr(google_oauth, "abort_browser_flow", lambda l: aborted.append(l))

    resp = client.delete(f"/api/providers/oauth/sessions/{session_id}", headers=HEADERS)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert aborted == [login]
    assert login.ready.is_set()
    assert session_id not in ws._oauth_sessions


def test_gemini_disconnect_clears_store_and_auth_state(monkeypatch):
    from agent import google_oauth
    from hermes_cli import auth as auth_mod

    calls = []
    monkeypatch.setattr(google_oauth, "clear_credentials", lambda: calls.append("file"))
    monkeypatch.setattr(auth_mod, "clear_provider_auth", lambda pid: calls.append(pid))

    resp = client.delete("/api/providers/oauth/google-gemini-cli", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "provider": "google-gemini-cli"}
    assert calls == ["file", "google-gemini-cli"]
