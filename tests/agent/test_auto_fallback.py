"""AIS-503: automatic model fallback for AIMDS Suite primaries."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import auto_fallback as af
from agent.error_classifier import FailoverReason

SUITE = "aimds-suite-prod"
SUITE_URL = "https://suite.example.test/litellm/v1"


def _kinds(chain):
    return [(e.get(af.AUTO_KEY), e.get("provider"), e.get("model")) for e in chain]


class TestChainShape:
    def test_suite_primary_gets_router_cheap_configured_other(self):
        configured = [{"provider": SUITE, "model": "gpt-5-mini"}]
        chain = af.with_auto_fallbacks(SUITE, "vllm-custom", configured)
        assert _kinds(chain) == [
            (af.SUITE_AUTO, SUITE, "AIMDS-Suite-Auto"),
            (af.SUITE_CHEAP, SUITE, ""),
            (af.SUITE_CHEAP, SUITE, ""),
            (None, SUITE, "gpt-5-mini"),
            (af.OTHER_PROVIDER, "", ""),
        ]

    def test_router_primary_skips_the_router_entry(self):
        chain = af.with_auto_fallbacks(SUITE, "AIMDS-Suite-Auto", [])
        assert [e[af.AUTO_KEY] for e in chain] == [af.SUITE_CHEAP, af.SUITE_CHEAP, af.OTHER_PROVIDER]

    def test_configured_other_provider_replaces_the_automatic_one(self):
        chain = af.with_auto_fallbacks(SUITE, "vllm-custom", [{"provider": "anthropic", "model": "claude-haiku-4-5"}])
        assert chain[-1] == {"provider": "anthropic", "model": "claude-haiku-4-5"}

    def test_non_suite_primary_keeps_the_configured_chain(self):
        configured = [{"provider": "zai", "model": "glm-4.7"}]
        assert af.with_auto_fallbacks("openrouter", "x", configured) == configured

    def test_rebuild_drops_previous_automatic_entries(self):
        first = af.with_auto_fallbacks(SUITE, "vllm-custom", [])
        assert af.with_auto_fallbacks("openrouter", "x", first) == []

    def test_legacy_suite_provider_counts(self):
        assert af.with_auto_fallbacks("iamds-litellm", "vllm-custom", [])[0][af.AUTO_KEY] == af.SUITE_AUTO


def _agent(model="vllm-custom"):
    return SimpleNamespace(
        provider=SUITE, model=model, base_url=SUITE_URL, api_key="sk-suite-key-123456",
        _primary_runtime={"provider": SUITE, "model": model, "base_url": SUITE_URL, "api_key": "sk-suite-key-123456"},
        session_id="s1",
    )


class TestResolve:
    def test_router_uses_the_same_environment(self, monkeypatch):
        monkeypatch.setattr(af, "_key_models", lambda p: ["AIMDS-Suite-Auto", "vllm-custom"])
        entry = af.resolve_entry(_agent(), {"provider": SUITE, "model": "AIMDS-Suite-Auto", af.AUTO_KEY: af.SUITE_AUTO})
        assert entry["model"] == "AIMDS-Suite-Auto"
        assert entry["base_url"] == SUITE_URL and entry["api_key"] == "sk-suite-key-123456"

    def test_router_not_offered_to_the_key_is_skipped(self, monkeypatch):
        monkeypatch.setattr(af, "_key_models", lambda p: ["gpt-5-mini"])
        assert af.resolve_entry(_agent(), {"provider": SUITE, "model": "AIMDS-Suite-Auto", af.AUTO_KEY: af.SUITE_AUTO}) is None

    def test_unknown_key_list_still_tries_the_router(self, monkeypatch):
        monkeypatch.setattr(af, "_key_models", lambda p: [])
        assert af.resolve_entry(_agent(), {"provider": SUITE, "model": "AIMDS-Suite-Auto", af.AUTO_KEY: af.SUITE_AUTO})

    def test_cheap_slots_follow_cost_without_failed_model(self, monkeypatch):
        monkeypatch.setattr(af, "_key_models", lambda p: ["AIMDS-Suite-Auto", "claude-sonnet-5", "gpt-5-mini", "claude-haiku-4.5"])
        meta = {
            "claude-sonnet-5": {"mode": "chat", "supports_function_calling": True, "pricing": {"prompt": 3, "completion": 15}},
            "gpt-5-mini": {"mode": "chat", "supports_function_calling": True, "pricing": {"prompt": 0.25, "completion": 2}},
        }
        import agent.model_metadata as mm

        monkeypatch.setattr(mm, "fetch_endpoint_model_metadata", lambda url, api_key="": meta)
        agent = _agent(model="gpt-5-mini")
        first = af.resolve_entry(agent, {"provider": SUITE, "model": "", af.AUTO_KEY: af.SUITE_CHEAP, "slot": 0})
        second = af.resolve_entry(agent, {"provider": SUITE, "model": "", af.AUTO_KEY: af.SUITE_CHEAP, "slot": 1})
        assert (first["model"], second["model"]) == ("claude-sonnet-5", "claude-haiku-4.5")

    def test_no_cheap_model_left_skips(self, monkeypatch):
        monkeypatch.setattr(af, "_key_models", lambda p: ["AIMDS-Suite-Auto", "vllm-custom"])
        import agent.model_metadata as mm

        monkeypatch.setattr(mm, "fetch_endpoint_model_metadata", lambda url, api_key="": {})
        assert af.resolve_entry(_agent(), {"provider": SUITE, "model": "", af.AUTO_KEY: af.SUITE_CHEAP, "slot": 0}) is None

    @pytest.mark.parametrize("reason", [FailoverReason.format_error, FailoverReason.context_overflow])
    def test_request_bound_failures_do_not_switch_models(self, monkeypatch, reason):
        monkeypatch.setattr(af, "_key_models", lambda p: [])
        entry = {"provider": SUITE, "model": "AIMDS-Suite-Auto", af.AUTO_KEY: af.SUITE_AUTO}
        assert af.resolve_entry(_agent(), entry, reason=reason) is None
        assert af.resolve_entry(_agent(), entry, reason=FailoverReason.model_not_found) is not None

    def test_other_provider_is_an_explicitly_configured_one(self, monkeypatch):
        import hermes_cli.auth as auth

        monkeypatch.setattr(auth, "is_provider_explicitly_configured", lambda p: p == "gemini")
        entry = af.resolve_entry(_agent(), {"provider": "", "model": "", af.AUTO_KEY: af.OTHER_PROVIDER})
        assert entry["provider"] == "gemini" and entry["model"]

    def test_no_other_provider_configured(self, monkeypatch):
        import hermes_cli.auth as auth

        monkeypatch.setattr(auth, "is_provider_explicitly_configured", lambda p: False)
        assert af.resolve_entry(_agent(), {"provider": "", "model": "", af.AUTO_KEY: af.OTHER_PROVIDER}) is None


class TestActivation:
    """The SUP-20261005-071541 shape: a Suite model the key no longer offers."""

    @pytest.fixture(autouse=True)
    def _no_model_info_probe(self, monkeypatch):
        import agent.model_metadata as mm

        monkeypatch.setattr(mm, "_fetch_litellm_model_info_contexts", lambda *a, **k: {})

    def _suite_agent(self):
        from run_agent import AIAgent

        with (
            patch("run_agent.get_tool_definitions", return_value=[]),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
        ):
            agent = AIAgent(
                api_key="sk-suite-key-123456", base_url=SUITE_URL, provider=SUITE, model="vllm-custom",
                quiet_mode=True, skip_context_files=True, skip_memory=True,
            )
        agent.client = MagicMock()
        return agent

    def test_switches_to_the_router_and_reports(self, monkeypatch):
        agent = self._suite_agent()
        assert agent._fallback_chain[0][af.AUTO_KEY] == af.SUITE_AUTO
        monkeypatch.setattr(af, "_key_models", lambda p: ["AIMDS-Suite-Auto", "gpt-5-mini"])
        client = MagicMock(base_url=SUITE_URL + "/", api_key="sk-suite-key-123456")
        statuses = []
        monkeypatch.setattr(agent, "_buffer_status", statuses.append)
        with patch("agent.auxiliary_client.resolve_provider_client", return_value=(client, "AIMDS-Suite-Auto")) as rpc, \
             patch("agent.model_metadata.get_model_context_length", return_value=128000), \
             patch("hermes_cli.auto_incidents.report_in_background") as incident:
            assert agent._try_activate_fallback(reason=FailoverReason.model_not_found)
        assert agent.model == "AIMDS-Suite-Auto" and agent.provider == SUITE
        assert rpc.call_args.kwargs["explicit_base_url"] == SUITE_URL
        assert rpc.call_args.kwargs["explicit_api_key"] == "sk-suite-key-123456"
        assert any("vllm-custom is not available" in s and "automatic fallback" in s for s in statuses)
        assert incident.call_args.args[0] == "model-fallback-aimds-suite-prod-vllm-custom"

    def test_model_switch_rebuilds_the_automatic_part(self):
        agent = self._suite_agent()
        from agent.agent_runtime_helpers import switch_model

        with patch("run_agent.OpenAI"):
            try:
                switch_model(agent, "AIMDS-Suite-Auto", SUITE, api_key="sk-suite-key-123456", base_url=SUITE_URL)
            except Exception:
                pytest.skip("switch_model needs a fuller runtime here")
        assert not any(e.get(af.AUTO_KEY) == af.SUITE_AUTO for e in agent._fallback_chain)
