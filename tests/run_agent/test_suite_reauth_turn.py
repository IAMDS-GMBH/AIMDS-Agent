"""AIS-525: a Suite key the LiteLLM no longer knows (e.g. after a staging
refresh, SUP-20261006-140017 / SUP-20261008-053048)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from run_agent import AIAgent

_TOKEN_GONE = (
    "Authentication Error, Invalid proxy server token passed. Received API Key = sk-...O88A, "
    "Key Hash (Token) =f51f…. Unable to find token in cache or `LiteLLM_VerificationTokenTable`"
)


def _auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://staging.suite.iamds.com/litellm/v1/chat/completions")
    response = httpx.Response(401, request=request, json={"error": {"message": _TOKEN_GONE}})
    return openai.AuthenticationError(_TOKEN_GONE, response=response, body={"error": {"message": _TOKEN_GONE}})


@pytest.fixture(autouse=True)
def _german(monkeypatch):
    from agent.i18n import reset_language_cache

    monkeypatch.setenv("HERMES_LANGUAGE", "de")
    monkeypatch.setattr(AIAgent, "_spawn_background_review", lambda self, *a, **k: None)
    reset_language_cache()
    yield
    reset_language_cache()


def _agent() -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
        # The context length would be read from the Suite's LiteLLM model info.
        patch("agent.context_compressor.get_model_context_length", return_value=128_000),
    ):
        agent = AIAgent(
            api_key="sk-test-1234567890",
            base_url="https://staging.suite.iamds.com/litellm/v1",
            provider="aimds-suite-staging",
            model="AIMDS-Suite-Auto",
            max_iterations=3,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def _run(agent):
    statuses = []
    agent._emit_status = lambda text, *a, **k: statuses.append(text)
    with (
        patch.object(agent, "_interruptible_api_call", side_effect=_auth_error()),
        patch.object(agent, "_try_refresh_iamds_client_credentials", return_value=False),
        patch.object(agent, "_try_activate_fallback", return_value=False),
        patch("hermes_cli.auto_incidents.report_auth_401") as report,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Hallo")
    return result, statuses, report


def test_lost_suite_login_tells_the_user_to_sign_in_again_and_reports_once():
    from hermes_cli import iamds_suite

    iamds_suite.clear_suite_auth_failure()
    first, statuses, report = _run(_agent())

    assert first["failed"] is True
    final = first["final_response"]
    assert final.startswith("Deine Anmeldung bei AIMDS-Suite (Staging) ist abgelaufen")
    assert "Neu anmelden" in final
    assert "sk-" not in final and "Key Hash" not in final
    assert any("Neu anmelden" in s for s in statuses)
    report.assert_called_once()
    assert iamds_suite.suite_reauth_pending("aimds-suite-staging")

    # Two days later, still not signed in again: a notice, but no new case.
    second, _, report_again = _run(_agent())
    assert second["final_response"].startswith("Deine Anmeldung bei AIMDS-Suite (Staging)")
    report_again.assert_not_called()

    # After a successful re-login the next loss is a new case again.
    iamds_suite.clear_suite_auth_failure("aimds-suite-staging")
    _, _, report_new = _run(_agent())
    report_new.assert_called_once()
