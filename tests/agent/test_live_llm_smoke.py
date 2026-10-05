"""Smoke test of a real chat completion through the agent's OpenAI client path.

Uses the ``live_llm`` fixture (tests/conftest.py, AIS-487): against the IAMDS
vLLM when ``VLLM_IAMDS_API_KEY`` is set and the endpoint answers, otherwise
against a loopback OpenAI-compatible fake. The assertions hold for both modes,
so the test stays green in forks and before the CI secret exists.
"""

from __future__ import annotations


def test_openai_client_chat_completion(live_llm):
    from openai import OpenAI

    client = OpenAI(base_url=live_llm.base_url, api_key=live_llm.api_key, timeout=60)
    resp = client.chat.completions.create(
        model=live_llm.model,
        messages=[{"role": "user", "content": "Reply with exactly the word: pong"}],
        max_tokens=256,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").lower()
    assert "pong" in text, f"{live_llm.describe()}: unexpected reply {text!r}"


def test_agent_conversation_round_trip(live_llm):
    """One AIAgent turn without tools: client construction, streaming request,
    response parsing and the final-response plumbing."""
    from run_agent import AIAgent

    agent = AIAgent(
        provider="custom",
        base_url=live_llm.base_url,
        api_key=live_llm.api_key,
        model=live_llm.model,
        enabled_toolsets=[],
        quiet_mode=True,
        save_trajectories=False,
        skip_context_files=True,
        skip_memory=True,
        max_iterations=2,
    )
    result = agent.run_conversation(
        user_message="Reply with exactly the word: pong",
        system_message="You are a test fixture. Answer with the single word requested.",
    )
    final = (result.get("final_response") or "") if isinstance(result, dict) else str(result)
    assert "pong" in final.lower(), f"{live_llm.describe()}: unexpected reply {final!r}"
