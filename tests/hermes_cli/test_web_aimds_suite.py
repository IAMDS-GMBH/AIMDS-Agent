"""Dashboard endpoints for the AIMDS-Suite provider status / re-auth (AIS-286)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hermes_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}


@pytest.fixture
def suite_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / ".env").write_text("", encoding="utf-8")
    for var in ("IAMDS_LITELLM_API_KEY", "IAMDS_LITELLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from hermes_cli import iamds_suite

    monkeypatch.setattr(iamds_suite, "_flag_path", lambda: home / "state" / "iamds_suite_auth.json")
    monkeypatch.setattr(iamds_suite, "_mcp_status_for", lambda base_url: {"name": "AIMDSSuiteMCP", "url": "", "url_matches": None, "connected": None})
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass
    return home


def test_status_endpoint_without_probe(suite_env):
    resp = client.get("/api/providers/aimds-suite/status", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    envs = {e["id"]: e for e in resp.json()["environments"]}
    assert set(envs) == {"aimds-suite-prod", "aimds-suite-staging", "aimds-suite-dev", "aimds-suite-localdev"}
    assert envs["aimds-suite-prod"]["state"] == "not_configured"
    assert envs["aimds-suite-prod"]["base_url"] == ""  # default host never shown as configured


def test_status_probe_requires_token(suite_env):
    assert client.get("/api/providers/aimds-suite/status?probe=true").status_code in (401, 403)


def test_status_reflects_runtime_flag_and_reauth_clears_it(suite_env, monkeypatch):
    from hermes_cli import iamds_suite

    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}})
    iamds_suite.mark_suite_auth_failure("aimds-suite-prod", 401, "token_not_found_in_db", source="llm")

    status = client.get("/api/status", headers=HEADERS).json()
    assert status["provider_auth"]["aimds-suite-prod"]["state"] == "needs_reauth"

    envs = {e["id"]: e for e in client.get("/api/providers/aimds-suite/status", headers=HEADERS).json()["environments"]}
    assert envs["aimds-suite-prod"]["reason"] == "runtime_401"

    monkeypatch.setattr(iamds_suite, "apply_reauth", lambda provider: {"provider": provider, "steps": {"flag_cleared": True}} if not iamds_suite.clear_suite_auth_failure(provider) else None)
    resp = client.post("/api/providers/aimds-suite/aimds-suite-prod/reauth-complete", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert client.get("/api/status", headers=HEADERS).json()["provider_auth"] == {}


def test_reauth_complete_rejects_unknown_env(suite_env):
    resp = client.post("/api/providers/aimds-suite/openrouter/reauth-complete", headers=HEADERS)
    assert resp.status_code == 400


def test_env_listing_treats_placeholder_secret_as_unset(suite_env, monkeypatch):
    monkeypatch.setattr("hermes_cli.web_server.load_env", lambda: {"IAMDS_LITELLM_API_KEY": "sk-", "IAMDS_LITELLM_BASE_URL": "https://suite.iamds.com/litellm/v1"})
    data = client.get("/api/env", headers=HEADERS).json()
    assert data["IAMDS_LITELLM_API_KEY"]["is_set"] is False
    assert data["IAMDS_LITELLM_BASE_URL"]["is_set"] is True


def test_put_config_strips_parse_error_marker_and_syncs_env(suite_env, monkeypatch):
    saved: dict = {}
    synced: dict = {}
    monkeypatch.setattr("hermes_cli.web_server.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.iamds_suite.sync_suite_env_from_providers", lambda cfg: synced.update({"called_with": json.loads(json.dumps(cfg))}) or {"IAMDS_LITELLM_BASE_URL": "set"})

    body = {"config": {"config_parse_error": None, "providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}}
    resp = client.put("/api/config", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert "config_parse_error" not in saved
    assert synced["called_with"]["providers"]["aimds-suite-prod"]["base_url"] == "https://suite.iamds.com/litellm/v1"
    assert resp.json()["env_sync"] == {"IAMDS_LITELLM_BASE_URL": "set"}


def test_validate_suite_key_probes_configured_environment(suite_env, monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}})
    seen: dict = {}

    def fake_probe(base_url, api_key, timeout=5.0):
        seen.update({"base_url": base_url, "api_key": api_key})
        return (401, "Unauthorized")

    monkeypatch.setattr("hermes_cli.iamds_suite.probe_suite_endpoint", fake_probe)
    resp = client.post("/api/providers/validate", json={"key": "IAMDS_LITELLM_API_KEY", "value": "sk-test-key-1234"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False and resp.json()["reachable"] is True
    assert seen == {"base_url": "https://suite.iamds.com/litellm/v1", "api_key": "sk-test-key-1234"}


def test_validate_suite_key_without_url_asks_for_configuration(suite_env, monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    resp = client.post("/api/providers/validate", json={"key": "IAMDS_LITELLM_STAGING_API_KEY", "value": "sk-test-key-1234"}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "base URL" in resp.json()["message"]
