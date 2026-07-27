from unittest.mock import MagicMock, patch

from agent.litellm_hub_client import fetch_litellm_hub_json, resolve_litellm_hub_settings


def _mock_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def test_fetch_litellm_hub_aliases_legacy_paths_and_strips_v1_suffix():
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _mock_response([{"id": "agent-1"}])

    settings = {
        "base_url": "http://localhost:4000/litellm/v1",
        "api_key": "",
        "timeout": 20,
    }

    with patch("httpx.get", side_effect=fake_get):
        payload, error = fetch_litellm_hub_json("agents", require_auth=False, settings=settings)

    assert error is None
    assert payload == [{"id": "agent-1"}]
    assert captured["url"] == "http://localhost:4000/litellm/public/agent_hub"


def test_fetch_litellm_hub_uses_skill_hub_path_directly():
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _mock_response([{"id": "skill-1"}])

    settings = {
        "base_url": "http://localhost:4000/litellm",
        "api_key": "",
        "timeout": 20,
    }

    with patch("httpx.get", side_effect=fake_get):
        payload, error = fetch_litellm_hub_json("skill_hub", require_auth=False, settings=settings)

    assert error is None
    assert payload == [{"id": "skill-1"}]
    assert captured["url"] == "http://localhost:4000/litellm/public/skill_hub"


def test_resolve_hub_settings_prefers_iamds_key_over_openai(monkeypatch):
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "iamds-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("LITELLM_KEY", raising=False)

    with patch("hermes_cli.config.load_config", return_value={"skills": {"litellm_hub": {}}}):
        settings = resolve_litellm_hub_settings()

    assert settings["api_key"] == "iamds-key"


def test_resolve_hub_settings_prefers_iamds_key_over_config_key(monkeypatch):
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "iamds-key")
    monkeypatch.delenv("LITELLM_KEY", raising=False)

    cfg = {"skills": {"litellm_hub": {"api_key": "stale-config-key"}}}
    with patch("hermes_cli.config.load_config", return_value=cfg):
        settings = resolve_litellm_hub_settings()

    assert settings["api_key"] == "iamds-key"


def test_resolve_hub_settings_prefers_env_file_over_stale_process_env(monkeypatch):
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "stale-process-key")
    monkeypatch.delenv("LITELLM_KEY", raising=False)

    cfg = {"skills": {"litellm_hub": {"api_key": "stale-config-key"}}}
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.load_env", return_value={"IAMDS_LITELLM_API_KEY": "file-key"}),
    ):
        settings = resolve_litellm_hub_settings()

    assert settings["api_key"] == "file-key"


def test_resolve_hub_settings_does_not_fallback_to_openai_or_provider_key(monkeypatch):
    monkeypatch.delenv("IAMDS_LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    cfg = {
        "skills": {"litellm_hub": {}},
        "providers": {"openai": {"api_key": "provider-key"}},
    }
    with patch("hermes_cli.config.load_config", return_value=cfg):
        settings = resolve_litellm_hub_settings()

    assert settings["api_key"] == ""


def test_resolve_hub_settings_matches_base_url_to_active_provider_key(monkeypatch):
    """Regression test: clicking "Entdecken" (agent hub) while the active
    provider is aimds-suite-staging must use the staging base_url together
    with the staging API key, not the prod key (IAMDS_LITELLM_API_KEY),
    which the staging LiteLLM proxy correctly rejects with 401.
    """
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "prod-key")
    monkeypatch.delenv("LITELLM_KEY", raising=False)

    cfg = {"skills": {"litellm_hub": {}}}
    staging_creds = {
        "provider": "aimds-suite-staging",
        "api_key": "staging-key",
        "base_url": "https://staging.suite.iamds.com/litellm/v1",
        "source": "IAMDS_LITELLM_STAGING_API_KEY",
    }
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.auth.get_active_provider", return_value="aimds-suite-staging"),
        patch("hermes_cli.auth.resolve_api_key_provider_credentials", return_value=staging_creds),
    ):
        settings = resolve_litellm_hub_settings()

    assert settings["base_url"] == "https://staging.suite.iamds.com/litellm"
    assert settings["api_key"] == "staging-key"


def test_resolve_hub_settings_explicit_override_wins_over_active_provider(monkeypatch):
    monkeypatch.delenv("IAMDS_LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_KEY", raising=False)

    cfg = {
        "skills": {
            "litellm_hub": {
                "base_url": "https://custom.example.com/litellm",
                "api_key": "custom-key",
            }
        }
    }
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.auth.get_active_provider", return_value="aimds-suite-staging"),
    ):
        settings = resolve_litellm_hub_settings()

    assert settings["base_url"] == "https://custom.example.com/litellm"
    assert settings["api_key"] == "custom-key"
