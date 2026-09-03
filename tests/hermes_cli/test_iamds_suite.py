"""hermes_cli.iamds_suite — one resolver, tri-state status, re-auth flag (AIS-286)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import iamds_suite as suite


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / ".env").write_text("", encoding="utf-8")
    for var in (
        "IAMDS_LITELLM_API_KEY", "IAMDS_LITELLM_BASE_URL", "OPENAI_BASE_URL",
        "IAMDS_LITELLM_STAGING_API_KEY", "IAMDS_LITELLM_STAGING_BASE_URL",
        "IAMDS_LITELLM_DEV_API_KEY", "IAMDS_LITELLM_DEV_BASE_URL",
        "IAMDS_LITELLM_LOCALDEV_API_KEY", "IAMDS_LITELLM_LOCALDEV_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass
    monkeypatch.setattr(suite, "_flag_path", lambda: home / "state" / "iamds_suite_auth.json")
    return home


# --------------------------------------------------------------------------- resolution

class TestResolveSuiteEndpoint:
    def test_config_beats_env_and_flags_mismatch(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://staging.suite.iamds.com/litellm/v1")
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        cfg = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}

        ep = suite.resolve_suite_endpoint("aimds-suite-prod", config=cfg)

        assert ep.base_url == "https://suite.iamds.com/litellm/v1"
        assert ep.base_url_source == "config"
        assert ep.env_mismatch is True
        assert ep.api_key == "sk-prod-key-1234"
        assert ep.key_source == "IAMDS_LITELLM_API_KEY"
        assert ep.configured is True

    def test_env_used_when_config_has_no_entry(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://suite.iamds.com/litellm/v1/")
        ep = suite.resolve_suite_endpoint("aimds-suite-prod", config={})
        assert ep.base_url == "https://suite.iamds.com/litellm/v1"
        assert ep.base_url_source == "env"
        assert ep.env_mismatch is False

    def test_default_is_never_configured(self, isolated_home):
        ep = suite.resolve_suite_endpoint("aimds-suite-prod", config={})
        assert ep.base_url_source == "default"
        assert ep.configured is False
        assert suite.resolve_suite_endpoint("aimds-suite-prod", config={}, allow_default=False).base_url == ""

    def test_legacy_slug_and_key_env_from_config(self, isolated_home, monkeypatch):
        monkeypatch.setenv("MY_SUITE_KEY", "sk-custom-key-9999")
        cfg = {"providers": {"iamds-litellm": {"base_url": "https://suite.iamds.com/litellm/v1", "key_env": "MY_SUITE_KEY"}}}
        ep = suite.resolve_suite_endpoint("iamds-litellm", config=cfg)
        assert ep.provider_id == "aimds-suite-prod"
        assert ep.base_url_source == "config"
        assert ep.api_key == "sk-custom-key-9999"
        assert ep.key_source == "config:key_env:MY_SUITE_KEY"

    def test_staging_never_borrows_the_prod_key(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-key-1234")
        ep = suite.resolve_suite_endpoint("aimds-suite-staging", config={})
        assert ep.api_key == ""
        assert ep.key_source == ""

    def test_prod_openai_base_url_compat(self, isolated_home, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://suite.iamds.com/litellm/v1")
        assert suite.resolve_suite_endpoint("aimds-suite-prod", config={}).base_url_source == "env"
        assert suite.resolve_suite_endpoint("aimds-suite-staging", config={}).base_url_source == "default"

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError):
            suite.resolve_suite_endpoint("openrouter")
        assert suite.canonical_suite_provider("iamds-litellm-dev") == "aimds-suite-dev"
        assert suite.is_suite_provider("nous") is False


class TestSyncEnv:
    def test_sets_and_removes_env_vars(self, isolated_home, monkeypatch):
        calls: list[tuple[str, str, str]] = []
        monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: calls.append(("set", k, v)))
        monkeypatch.setattr("hermes_cli.config.remove_env_value", lambda k: calls.append(("rm", k, "")) or True)
        monkeypatch.setenv("IAMDS_LITELLM_STAGING_BASE_URL", "https://old-staging.example/litellm/v1")

        cfg = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}
        changes = suite.sync_suite_env_from_providers(cfg)

        assert changes["IAMDS_LITELLM_BASE_URL"] == "set"
        assert changes["IAMDS_LITELLM_STAGING_BASE_URL"] == "removed"
        assert ("set", "IAMDS_LITELLM_BASE_URL", "https://suite.iamds.com/litellm/v1") in calls
        assert ("rm", "IAMDS_LITELLM_STAGING_BASE_URL", "") in calls

    def test_noop_when_in_sync(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://suite.iamds.com/litellm/v1")
        monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: pytest.fail("must not write"))
        cfg = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}
        assert suite.sync_suite_env_from_providers(cfg) == {}


# --------------------------------------------------------------------------- status

def test_litellm_model_info_url_variants():
    assert suite.litellm_model_info_url("https://h") == "https://h/litellm/model/info"
    assert suite.litellm_model_info_url("https://h/litellm/") == "https://h/litellm/model/info"
    assert suite.litellm_model_info_url("https://h/litellm/v1") == "https://h/litellm/model/info"
    assert suite.litellm_model_info_url("") == ""


class TestStatusMatrix:
    CFG = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}

    def test_not_configured(self, isolated_home):
        st = suite.suite_environment_status("aimds-suite-staging", config={}, include_mcp=False)
        assert (st["state"], st["reason"]) == ("not_configured", "url_missing")
        assert st["base_url"] == ""  # default host is not shown as configured

    def test_key_missing(self, isolated_home):
        st = suite.suite_environment_status("aimds-suite-prod", config=self.CFG, include_mcp=False)
        assert (st["state"], st["reason"]) == ("needs_reauth", "key_missing")
        assert st["base_url"] == "https://suite.iamds.com/litellm/v1"

    def test_url_missing_but_key_present(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_STAGING_API_KEY", "sk-staging-key-1234")
        st = suite.suite_environment_status("aimds-suite-staging", config={}, include_mcp=False)
        assert (st["state"], st["reason"]) == ("needs_reauth", "url_missing")

    def test_env_mismatch(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://staging.suite.iamds.com/litellm/v1")
        st = suite.suite_environment_status("aimds-suite-prod", config=self.CFG, include_mcp=False)
        assert (st["state"], st["reason"]) == ("needs_reauth", "env_mismatch")

    def test_placeholder_key_is_missing(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-")
        st = suite.suite_environment_status("aimds-suite-prod", config=self.CFG, include_mcp=False)
        assert st["reason"] == "key_missing"

    @pytest.mark.parametrize(
        ("code", "state", "reason"),
        [(200, "connected", "ok"), (429, "connected", "ok"), (401, "needs_reauth", "http_401"),
         (403, "needs_reauth", "http_403"), (503, "unreachable", "http_503"), (None, "unreachable", "network")],
    )
    def test_probe_outcomes(self, isolated_home, monkeypatch, code, state, reason):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        st = suite.suite_environment_status(
            "aimds-suite-prod", config=self.CFG, probe=True, include_mcp=False,
            probe_fn=lambda url, key: (code, "" if code else "ConnectionRefused"),
        )
        assert (st["state"], st["reason"]) == (state, reason)
        assert st["http_status"] == code

    def test_probe_skipped_reports_connected(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        st = suite.suite_environment_status("aimds-suite-prod", config=self.CFG, include_mcp=False)
        assert (st["state"], st["reason"]) == ("connected", "probe_skipped")

    def test_runtime_flag_wins_over_probe(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        suite.mark_suite_auth_failure("aimds-suite-prod", 401, "token_not_found_in_db", source="llm")
        st = suite.suite_environment_status(
            "aimds-suite-prod", config=self.CFG, probe=True, include_mcp=False, probe_fn=lambda u, k: (200, ""),
        )
        assert (st["state"], st["reason"]) == ("needs_reauth", "runtime_401")
        assert st["runtime_auth_failure"]["source"] == "llm"

        suite.clear_suite_auth_failure("aimds-suite-prod")
        st = suite.suite_environment_status(
            "aimds-suite-prod", config=self.CFG, probe=True, include_mcp=False, probe_fn=lambda u, k: (200, ""),
        )
        assert st["state"] == "connected"

    def test_all_statuses_lists_every_environment(self, isolated_home, monkeypatch):
        monkeypatch.setattr(suite, "_mcp_status_for", lambda base_url: {"name": "AIMDSSuiteMCP", "url": "", "url_matches": None, "connected": None})
        payload = suite.all_suite_statuses(config={})
        assert [e["id"] for e in payload["environments"]] == list(suite.SUITE_ENVIRONMENTS)
        assert "mcp" in payload["environments"][0]


# --------------------------------------------------------------------------- flag file

def test_flag_file_roundtrip_and_legacy_slug(isolated_home):
    suite.mark_suite_auth_failure("iamds-litellm-dev", 401, "nope", source="mcp")
    flags = suite.suite_auth_failures()
    assert set(flags) == {"aimds-suite-dev"}
    assert flags["aimds-suite-dev"]["state"] == "needs_reauth"
    path = isolated_home / "state" / "iamds_suite_auth.json"
    assert json.loads(path.read_text(encoding="utf-8"))["aimds-suite-dev"]["source"] == "mcp"
    suite.mark_suite_auth_failure("openrouter", 401, "ignored", source="llm")  # not a suite provider → no-op
    assert set(suite.suite_auth_failures()) == {"aimds-suite-dev"}
    suite.clear_suite_auth_failure()
    assert suite.suite_auth_failures() == {}


# --------------------------------------------------------------------------- apply_reauth

def test_apply_reauth_orchestrates_pool_mcp_and_sessions(isolated_home, monkeypatch):
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-new-key-5678")
    cfg = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    suite.mark_suite_auth_failure("aimds-suite-prod", 401, "old key", source="llm")

    class FakePool:
        def reset_statuses(self):
            return 2

    calls: dict[str, object] = {}
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: calls.setdefault("pool", provider) and FakePool())

    import tools.mcp_tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "reload_provider_mcp_servers", lambda **kw: calls.setdefault("mcp", kw))
    monkeypatch.setattr(mcp_tool, "discover_mcp_tools", lambda: calls.setdefault("discover", True))

    import tui_gateway.server as gw

    monkeypatch.setattr(gw, "refresh_iamds_credentials_for_sessions", lambda provider: calls.setdefault("sessions", provider) and 1)

    result = suite.apply_reauth("aimds-suite-prod")

    assert result["steps"]["flag_cleared"] is True
    assert suite.suite_auth_failures() == {}
    assert result["steps"]["pool_reset"] == 2
    assert calls["mcp"] == {"provider": "aimds-suite-prod", "new_base_url": "https://suite.iamds.com/litellm/v1", "new_api_key": "sk-new-key-5678"}
    assert result["steps"]["mcp_reloaded"] is True
    assert result["steps"]["sessions_refreshed"] == 1
    assert result["endpoint"]["api_key"].startswith("…")  # redacted


def test_apply_reauth_skips_mcp_without_credentials(isolated_home, monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    monkeypatch.setattr("agent.credential_pool.load_pool", lambda provider: None)
    import tui_gateway.server as gw

    monkeypatch.setattr(gw, "refresh_iamds_credentials_for_sessions", lambda provider: 0)
    result = suite.apply_reauth("aimds-suite-staging")
    assert result["steps"]["mcp_reloaded"] is False
    assert result["steps"]["pool_reset"] == 0
