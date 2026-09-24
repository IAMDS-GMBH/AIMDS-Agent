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


# --------------------------------------------------------------------------- ntfy (AIS-232)

import io as _io
import json as _json
from unittest.mock import patch as _patch


def test_suite_ntfy_urls():
    assert suite.suite_root_url("https://dev.suite.iamds.com/litellm/v1/") == "https://dev.suite.iamds.com"
    assert suite.suite_root_url("https://suite.iamds.com/litellm/mcp") == "https://suite.iamds.com"
    assert suite.suite_root_url("http://localhost:8000/v1") == "http://localhost:8000"
    assert suite.suite_ntfy_url("https://suite.iamds.com/litellm/v1") == "https://suite.iamds.com/ntfy"
    assert suite.litellm_key_info_url("https://suite.iamds.com/litellm/v1") == "https://suite.iamds.com/litellm/key/info"
    assert suite.suite_ntfy_url("") == "" and suite.litellm_key_info_url("") == ""
    assert suite.ntfy_private_topic("a1b2") == "private-a1b2"
    assert suite.ntfy_private_topic("john.doe@iamds.com") == "private-john_doe_iamds_com"
    assert suite.ntfy_private_topic("") == ""


class TestPrimarySuiteProvider:
    def test_model_provider_wins_when_it_has_a_key(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_DEV_API_KEY", "sk-dev-key-1234")
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        cfg = {"model": {"provider": "aimds-suite-dev"}, "providers": {"aimds-suite-dev": {"base_url": "https://dev.suite.iamds.com/litellm/v1"}}}
        assert suite.primary_suite_provider(cfg) == "aimds-suite-dev"

    def test_falls_back_to_first_configured_env_with_key(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_STAGING_BASE_URL", "https://staging.suite.iamds.com/litellm/v1")
        monkeypatch.setenv("IAMDS_LITELLM_STAGING_API_KEY", "sk-staging-key-1234")
        cfg = {"model": {"provider": "openrouter"}}
        assert suite.primary_suite_provider(cfg) == "aimds-suite-staging"

    def test_none_without_any_suite_key(self, isolated_home):
        assert suite.primary_suite_provider({"model": {"provider": "openrouter"}}) is None


class TestResolveSuiteNtfy:
    def _urlopen(self, payload, calls):
        class _Resp(_io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(request, timeout=0):
            calls.append(request.full_url)
            assert request.get_header("Authorization") == "Bearer sk-dev-key-1234"
            return _Resp(_json.dumps(payload).encode("utf-8"))
        return fake

    def test_resolves_server_token_and_private_topic_and_caches(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_DEV_BASE_URL", "https://dev.suite.iamds.com/litellm/v1")
        monkeypatch.setenv("IAMDS_LITELLM_DEV_API_KEY", "sk-dev-key-1234")
        cfg = {"model": {"provider": "aimds-suite-dev"}}
        calls = []
        payload = {"key": "sk-…", "info": {"user_id": "u-42", "metadata": {"ntfy_topics": ["general/*", "alerts/*"]}}}
        with _patch.object(suite.urllib.request, "urlopen", side_effect=self._urlopen(payload, calls)):
            first = suite.resolve_suite_ntfy(config=cfg)
            second = suite.resolve_suite_ntfy(config=cfg)
        assert first.server_url == "https://dev.suite.iamds.com/ntfy"
        assert first.token == "sk-dev-key-1234" and first.user_id == "u-42" and first.topic == "private-u-42"
        assert first.topics == ["general/*", "alerts/*"] and first.user_source == "key_info"
        assert calls == ["https://dev.suite.iamds.com/litellm/key/info"]  # second call came from the cache
        assert second.user_source == "cache" and second.topic == "private-u-42"
        redacted = first.to_dict()["token"]
        assert redacted != first.token and "sk-dev-key-1234" not in redacted
        assert (suite._ntfy_cache_path()).exists()

    def test_key_info_failure_leaves_topic_empty(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://suite.iamds.com/litellm/v1")
        with _patch.object(suite.urllib.request, "urlopen", side_effect=OSError("down")):
            res = suite.resolve_suite_ntfy(config={"model": {"provider": "aimds-suite-prod"}})
        assert res.server_url == "https://suite.iamds.com/ntfy" and res.token == "sk-prod-key-1234"
        assert res.topic == "" and res.user_source == "no_user_id"

    def test_none_without_suite(self, isolated_home):
        assert suite.resolve_suite_ntfy(config={"model": {"provider": "openai"}}) is None

    def test_cache_is_bound_to_the_key(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://suite.iamds.com/litellm/v1")
        cfg = {"model": {"provider": "aimds-suite-prod"}}
        calls = []
        with _patch.object(suite.urllib.request, "urlopen", side_effect=self._urlopen({"info": {"user_id": "u-1"}}, calls)):
            suite.resolve_suite_ntfy(config=cfg)
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-dev-key-1234")  # re-auth → new key → cache miss
        with _patch.object(suite.urllib.request, "urlopen", side_effect=self._urlopen({"info": {"user_id": "u-2"}}, calls)):
            res = suite.resolve_suite_ntfy(config=cfg)
        assert res.user_id == "u-2" and len(calls) == 2


# --------------------------------------------------------------------------- rebind_suite_mcp_for_model

def _fake_ep(provider_id, base_url, api_key="sk-x"):
    return suite.SuiteEndpoint(
        provider_id=provider_id, label=provider_id, key_env="K", url_env="U",
        base_url=base_url, base_url_source="env", api_key=api_key,
    )


def _patch_rebind(monkeypatch, *, endpoints, current_mcp_url):
    """endpoints: {provider_id: SuiteEndpoint}; resolve_suite_endpoint returns them."""
    monkeypatch.setattr(suite, "resolve_suite_endpoint", lambda slug, **kw: endpoints[suite.canonical_suite_provider(slug) or "aimds-suite-prod"])
    monkeypatch.setattr(suite, "_load_config_safe", lambda config=None: {"mcp_servers": {"AIMDSSuiteMCP": {"url": current_mcp_url}}})
    import tools.mcp_tool as mcp_tool
    calls = {}
    def _fake_reload(**kw):
        calls["mcp"] = kw
        return ["tool_a", "tool_b"]
    monkeypatch.setattr(mcp_tool, "reload_provider_mcp_servers", _fake_reload)
    monkeypatch.setattr(mcp_tool, "discover_mcp_tools", lambda: calls.setdefault("discover", True))
    return calls


def test_rebind_suite_model_repoints_to_that_instance(monkeypatch):
    eps = {"aimds-suite-staging": _fake_ep("aimds-suite-staging", "https://staging.suite.iamds.com/litellm/v1", "sk-staging")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    tools = suite.rebind_suite_mcp_for_model("aimds-suite-staging")
    assert tools == ["tool_a", "tool_b"]
    assert calls["mcp"]["provider"] == "aimds-suite-staging"
    assert calls["mcp"]["new_base_url"] == "https://staging.suite.iamds.com/litellm/v1"
    assert calls["mcp"]["new_api_key"] == "sk-staging"


def test_rebind_3rd_party_from_staging_goes_back_to_prod(monkeypatch):
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", "sk-prod")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://staging.suite.iamds.com/litellm/mcp/")
    tools = suite.rebind_suite_mcp_for_model("anthropic")  # 3rd-party → prod
    assert tools == ["tool_a", "tool_b"]
    assert calls["mcp"]["provider"] == "aimds-suite-prod"
    assert calls["mcp"]["new_base_url"] == "https://suite.iamds.com/litellm/v1"


def test_rebind_3rd_party_already_prod_is_a_noop(monkeypatch):
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", "sk-prod")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    assert suite.rebind_suite_mcp_for_model("google-gemini-cli") == []
    assert "mcp" not in calls  # same host → no reconnect churn


def test_rebind_without_prod_key_leaves_mcp_untouched(monkeypatch):
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", api_key="")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://staging.suite.iamds.com/litellm/mcp/")
    assert suite.rebind_suite_mcp_for_model("anthropic") == []
    assert "mcp" not in calls


def test_rebind_reload_tools_false_skips_discovery(monkeypatch):
    """The boot-time reconcile path (tools.mcp_tool.discover_mcp_tools calling
    this with reload_tools=False) must not trigger the inner discover_mcp_tools
    call -- that's the direct anti-recursion assertion (AIS-378)."""
    eps = {"aimds-suite-staging": _fake_ep("aimds-suite-staging", "https://staging.suite.iamds.com/litellm/v1", "sk-staging")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    tools = suite.rebind_suite_mcp_for_model("aimds-suite-staging", reload_tools=False)
    assert tools == ["tool_a", "tool_b"]
    assert "mcp" in calls  # the reload itself still happened
    assert "discover" not in calls


def test_rebind_reload_tools_default_still_discovers(monkeypatch):
    """Pins the four existing in-session /model-switch call sites' behavior,
    which all rely on the default reload_tools=True."""
    eps = {"aimds-suite-staging": _fake_ep("aimds-suite-staging", "https://staging.suite.iamds.com/litellm/v1", "sk-staging")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    tools = suite.rebind_suite_mcp_for_model("aimds-suite-staging")
    assert tools == ["tool_a", "tool_b"]
    assert calls.get("discover") is True


def test_rebind_failure_logs_warning(monkeypatch, caplog):
    eps = {"aimds-suite-staging": _fake_ep("aimds-suite-staging", "https://staging.suite.iamds.com/litellm/v1", "sk-staging")}
    _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    import tools.mcp_tool as mcp_tool

    def _boom(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_tool, "reload_provider_mcp_servers", _boom)
    with caplog.at_level("WARNING", logger="hermes_cli.iamds_suite"):
        result = suite.rebind_suite_mcp_for_model("aimds-suite-staging")
    assert result == []
    assert any("rebind_suite_mcp_for_model failed" in r.message for r in caplog.records)


def test_rebind_noop_paths_are_silent(monkeypatch, caplog):
    """Regression guard: bumping the failure log to WARNING must not turn the
    two benign no-op paths (already correct, no usable key) into log spam."""
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", "sk-prod")}
    _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp/")
    with caplog.at_level("WARNING", logger="hermes_cli.iamds_suite"):
        assert suite.rebind_suite_mcp_for_model("google-gemini-cli") == []
    assert not any(r.levelname == "WARNING" for r in caplog.records)

    caplog.clear()
    eps2 = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", api_key="")}
    _patch_rebind(monkeypatch, endpoints=eps2, current_mcp_url="https://staging.suite.iamds.com/litellm/mcp/")
    with caplog.at_level("WARNING", logger="hermes_cli.iamds_suite"):
        assert suite.rebind_suite_mcp_for_model("anthropic") == []
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_rebind_trailing_slash_only_difference_is_a_noop(monkeypatch):
    """_build_iamds_mcp_url always appends a trailing slash; _norm_url strips
    one -- a cosmetic-only difference must not force a reconnect (that would
    become 'reconnect on every boot' once this runs there too)."""
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", "sk-prod")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/litellm/mcp")
    assert suite.rebind_suite_mcp_for_model("google-gemini-cli") == []
    assert "mcp" not in calls


def test_rebind_path_only_mismatch_now_triggers_reconnect(monkeypatch):
    """Same host, different path -- previously a no-op under the old
    hostname-only comparison, now correctly detected (AIS-378 Step 4)."""
    eps = {"aimds-suite-prod": _fake_ep("aimds-suite-prod", "https://suite.iamds.com/litellm/v1", "sk-prod")}
    calls = _patch_rebind(monkeypatch, endpoints=eps, current_mcp_url="https://suite.iamds.com/some/other/path")
    tools = suite.rebind_suite_mcp_for_model("google-gemini-cli")
    assert tools == ["tool_a", "tool_b"]
    assert calls["mcp"]["provider"] == "aimds-suite-prod"


# --------------------------------------------------------------------------- periodic health check (AIS-394)

class TestSuiteHealthCheck:
    CFG = {"providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}

    def test_not_configured_skips_probe_entirely(self, isolated_home):
        calls = []
        result = suite.check_suite_environment_health(
            "aimds-suite-staging", probe_fn=lambda u, k: calls.append((u, k)) or (200, "")
        )
        assert result["outcome"] == "not_configured"
        assert calls == []

    def test_unreachable_is_not_an_auth_problem(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)
        suite.mark_suite_auth_failure("aimds-suite-prod", 401, "old failure", source="llm")

        result = suite.check_suite_environment_health(
            "aimds-suite-prod", probe_fn=lambda u, k: (None, "ConnectionRefused")
        )

        assert result["outcome"] == "unreachable"
        # a network blip must never clear or overwrite an existing auth flag
        assert suite.suite_auth_failures()["aimds-suite-prod"]["message"] == "old failure"

    def test_newly_broken_then_still_broken_does_not_move_since(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)

        first = suite.check_suite_environment_health(
            "aimds-suite-prod", probe_fn=lambda u, k: (401, "unauthorized")
        )
        assert first["outcome"] == "newly_broken"
        since_first = suite.suite_auth_failures()["aimds-suite-prod"]["since"]

        second = suite.check_suite_environment_health(
            "aimds-suite-prod", probe_fn=lambda u, k: (401, "unauthorized")
        )
        assert second["outcome"] == "still_broken"
        assert suite.suite_auth_failures()["aimds-suite-prod"]["since"] == since_first

    def test_self_heal_recovers_on_key_rotation(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-old-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)

        reauth_calls: list[str] = []
        monkeypatch.setattr(suite, "apply_reauth", lambda provider, **kw: reauth_calls.append(provider))

        def rotate_key():
            monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-new-key-5678")

        monkeypatch.setattr("hermes_cli.config.invalidate_env_cache", lambda: None)
        monkeypatch.setattr("hermes_cli.env_loader.load_hermes_dotenv", rotate_key)

        def fake_probe(url, key):
            return (200, "") if key == "sk-new-key-5678" else (401, "unauthorized")

        result = suite.check_suite_environment_health("aimds-suite-prod", probe_fn=fake_probe)

        assert result["outcome"] == "recovered_self_heal"
        assert result["http_status"] == 200
        assert reauth_calls == ["aimds-suite-prod"]
        assert suite.suite_auth_failures() == {}  # never had to be flagged at all

    def test_still_broken_when_self_heal_finds_no_new_key(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-dead-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)
        monkeypatch.setattr("hermes_cli.config.invalidate_env_cache", lambda: None)
        monkeypatch.setattr("hermes_cli.env_loader.load_hermes_dotenv", lambda: None)  # no rotation happens

        result = suite.check_suite_environment_health(
            "aimds-suite-prod", probe_fn=lambda u, k: (401, "unauthorized")
        )

        assert result["outcome"] == "newly_broken"
        assert suite.suite_auth_failures()["aimds-suite-prod"]["source"] == "health_check"

    def test_recovered_without_key_change_clears_flag(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)
        suite.mark_suite_auth_failure("aimds-suite-prod", 401, "was broken", source="llm")

        reauth_calls: list[str] = []
        monkeypatch.setattr(suite, "apply_reauth", lambda provider, **kw: reauth_calls.append(provider))

        result = suite.check_suite_environment_health(
            "aimds-suite-prod", probe_fn=lambda u, k: (200, "")
        )

        assert result["outcome"] == "recovered"
        assert reauth_calls == ["aimds-suite-prod"]

    def test_ok_when_healthy_and_never_flagged(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: self.CFG)
        result = suite.check_suite_environment_health("aimds-suite-prod", probe_fn=lambda u, k: (200, ""))
        assert result["outcome"] == "ok"

    def test_sweep_isolates_per_environment_failures(self, isolated_home, monkeypatch):
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-prod-key-1234")
        monkeypatch.setenv("IAMDS_LITELLM_STAGING_API_KEY", "sk-staging-key-1234")
        cfg = {
            "providers": {
                "aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"},
                "aimds-suite-staging": {"base_url": "https://staging.suite.iamds.com/litellm/v1"},
            }
        }
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)

        def flaky_probe(url, key):
            if "staging" in url:
                raise RuntimeError("boom")
            return (200, "")

        results = suite.run_suite_health_sweep(probe_fn=flaky_probe)
        by_provider = {r["provider"]: r for r in results}

        assert len(results) == len(suite.SUITE_ENVIRONMENTS)
        assert by_provider["aimds-suite-prod"]["outcome"] == "ok"
        assert by_provider["aimds-suite-staging"]["outcome"] == "error"


class TestMaybeRunSuiteHealthCheck:
    @pytest.fixture(autouse=True)
    def _state_file(self, isolated_home, monkeypatch):
        monkeypatch.setattr(
            suite, "_suite_health_check_state_file", lambda: isolated_home / "state" / "iamds_suite_health_check.json"
        )

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("HERMES_SUITE_HEALTH_CHECK_ENABLED", "0")
        assert suite.maybe_run_suite_health_check() is None

    def test_interval_gate_and_force(self, monkeypatch):
        monkeypatch.setenv("HERMES_SUITE_HEALTH_CHECK_INTERVAL_SECONDS", "1800")
        t0 = 1_000_000.0

        assert suite.maybe_run_suite_health_check(now=t0) is not None  # no prior state -> due
        assert suite.maybe_run_suite_health_check(now=t0 + 60) is None  # not due yet
        assert suite.maybe_run_suite_health_check(now=t0 + 60, force=True) is not None  # forced bypass
        assert suite.maybe_run_suite_health_check(now=t0 + 1900) is not None  # interval elapsed
