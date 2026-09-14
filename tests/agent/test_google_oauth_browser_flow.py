"""Split Gemini browser login: ``begin_browser_flow`` / ``finish_browser_flow`` (AIS-325).

The dashboard's loopback worker needs the bind + authorize-URL half without
the terminal UX (prints, browser, paste fallback); ``start_oauth_flow`` keeps
its behaviour by composing both halves.
"""

from __future__ import annotations

import threading

import pytest

from agent import google_oauth as g


class _FakeServer:
    def __init__(self):
        self.shutdown_calls = 0
        self.closed = 0
        self.server_address = ("127.0.0.1", 8085)

    def serve_forever(self):
        return None

    def shutdown(self):
        self.shutdown_calls += 1

    def server_close(self):
        self.closed += 1


@pytest.fixture
def fake_login_env(monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(g, "_require_client_id", lambda: "client-id")
    monkeypatch.setattr(g, "_get_client_secret", lambda: "client-secret")
    monkeypatch.setattr(g, "_bind_callback_server", lambda preferred_port=8085: (server, 8085))
    persisted = {}

    def _persist(token_resp, *, project_id=""):
        persisted.update(token_resp)
        return g.GoogleCredentials(
            access_token=token_resp["access_token"],
            refresh_token=token_resp["refresh_token"],
            expires_ms=1,
            email="u@example.com",
            project_id=project_id,
        )

    monkeypatch.setattr(g, "_persist_token_response", _persist)
    monkeypatch.setattr(
        g,
        "exchange_code",
        lambda code, verifier, redirect_uri, *, client_id, client_secret: {
            "access_token": f"at-{code}",
            "refresh_token": "rt",
            "expires_in": 3600,
            "_verifier": verifier,
            "_redirect": redirect_uri,
        },
    )
    return server, persisted


def _deliver_callback(login: g.GoogleBrowserLogin, *, code=None, error=None):
    g._OAuthCallbackHandler.captured_code = code
    g._OAuthCallbackHandler.captured_error = error
    login.ready.set()


def test_begin_builds_google_authorize_url_and_serves_callback(fake_login_env):
    server, _ = fake_login_env
    login = g.begin_browser_flow(project_id="proj")
    try:
        assert login.auth_url.startswith(g.AUTH_ENDPOINT + "?")
        assert "code_challenge=" in login.auth_url
        assert f"state={login.state}" in login.auth_url
        assert login.redirect_uri == f"http://127.0.0.1:8085{g.CALLBACK_PATH}"
        assert login.project_id == "proj"
        assert g._OAuthCallbackHandler.expected_state == login.state
        assert isinstance(login.ready, threading.Event) and not login.ready.is_set()
    finally:
        g.abort_browser_flow(login)
    assert server.shutdown_calls == 1 and server.closed == 1


def test_finish_exchanges_code_and_persists(fake_login_env):
    server, persisted = fake_login_env
    login = g.begin_browser_flow()
    _deliver_callback(login, code="abc")

    creds = g.finish_browser_flow(login, timeout=1)

    assert creds.access_token == "at-abc"
    assert persisted["_verifier"] == login.verifier
    assert persisted["_redirect"] == login.redirect_uri
    assert server.shutdown_calls == 1  # listener released after the callback


def test_finish_raises_on_callback_error(fake_login_env):
    login = g.begin_browser_flow()
    _deliver_callback(login, error="access_denied")

    with pytest.raises(g.GoogleOAuthError) as exc:
        g.finish_browser_flow(login, timeout=1)
    assert exc.value.code == "google_oauth_authorization_failed"


def test_finish_times_out_without_prompting(fake_login_env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("finish must never prompt"))
    login = g.begin_browser_flow()

    with pytest.raises(g.GoogleOAuthError) as exc:
        g.finish_browser_flow(login, timeout=0.01)
    assert exc.value.code == "google_oauth_timeout"


def test_start_oauth_flow_composes_halves_and_keeps_paste_fallback(fake_login_env, monkeypatch, capsys):
    monkeypatch.setattr(g, "_is_headless", lambda: False)
    monkeypatch.setattr(g, "load_credentials", lambda: None)
    monkeypatch.setattr(g, "_prompt_paste_fallback", lambda: "pasted-code")

    creds = g.start_oauth_flow(open_browser=False, callback_wait_seconds=0.01)

    assert creds.access_token == "at-pasted-code"
    assert "Opening your browser" in capsys.readouterr().out
