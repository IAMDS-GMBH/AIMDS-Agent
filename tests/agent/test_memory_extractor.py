"""Tests for agent/memory_extractor.py — LLM-assisted memory extraction."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent.memory_extractor import (
    _build_extraction_messages,
    _parse_extraction_response,
    should_attempt_extraction,
    spawn_memory_extraction_thread,
)


def test_build_extraction_messages_structure():
    msgs = _build_extraction_messages("I prefer dark mode.", "Noted, I'll remember that.")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "I prefer dark mode." in msgs[1]["content"]
    assert "Noted, I'll remember that." in msgs[1]["content"]


def test_parse_extraction_response_valid():
    payload = json.dumps([
        {"title": "Language", "content": "User prefers Spanish.", "type": "profile", "scope": "user", "tags": ["language"]},
    ])
    facts = _parse_extraction_response(payload)
    assert len(facts) == 1
    assert facts[0]["title"] == "Language"
    assert facts[0]["scope"] == "user"
    assert facts[0]["type"] == "profile"


def test_parse_extraction_response_empty_array():
    facts = _parse_extraction_response("[]")
    assert facts == []


def test_parse_extraction_response_empty_string():
    assert _parse_extraction_response("") == []


def test_parse_extraction_response_invalid_json():
    assert _parse_extraction_response("not json at all") == []


def test_parse_extraction_response_strips_markdown_fence():
    payload = "```json\n" + json.dumps([
        {"title": "Tone", "content": "User prefers concise answers.", "type": "profile", "scope": "user", "tags": ["tone"]}
    ]) + "\n```"
    facts = _parse_extraction_response(payload)
    assert len(facts) == 1
    assert facts[0]["title"] == "Tone"


def test_parse_extraction_response_caps_at_max():
    items = [
        {"title": f"Fact {i}", "content": f"Content {i}", "type": "notes", "scope": "project", "tags": []}
        for i in range(10)
    ]
    facts = _parse_extraction_response(json.dumps(items))
    from agent.memory_extractor import MAX_FACTS_PER_TURN
    assert len(facts) <= MAX_FACTS_PER_TURN


def test_parse_extraction_response_filters_missing_fields():
    payload = json.dumps([
        {"title": "", "content": "No title, should be skipped.", "type": "notes", "scope": "project", "tags": []},
        {"title": "Valid", "content": "Has both fields.", "type": "notes", "scope": "project", "tags": []},
    ])
    facts = _parse_extraction_response(payload)
    assert len(facts) == 1
    assert facts[0]["title"] == "Valid"


def test_should_attempt_extraction_short_text():
    assert not should_attempt_extraction("hi", "ok")


def test_should_attempt_extraction_short_user_fact_statement():
    assert should_attempt_extraction(
        "I work mostly on backend APIs in this repo.",
        "Got it.",
    )


def test_should_attempt_extraction_long_text():
    # Need >800 chars to bypass regex filter and go straight to extraction
    long_user = "I really want to build a microservices architecture with Docker Compose for local development. " * 5
    long_asst = "That makes sense given your team size. Docker Compose is great for local dev parity with production. " * 5
    assert should_attempt_extraction(long_user, long_asst)


def test_should_attempt_extraction_preference_phrasing():
    # The regex looks for "you prefer..." in the combined text (assistant echoing back preference)
    assert should_attempt_extraction(
        "Can you remember how I like my code formatted?",
        "Of course! You prefer TypeScript with 2-space indentation and strict null checks enabled across all your projects."
    )


def test_spawn_memory_extraction_thread_no_crash_on_bad_agent():
    """spawn_memory_extraction_thread must never raise, even with a broken agent."""
    bad_agent = SimpleNamespace(client=None, model=None)
    spawn_memory_extraction_thread(
        bad_agent,
        user_message="I prefer dark mode.",
        assistant_message="Noted, I'll remember you prefer dark mode.",
        effective_task_id="",
    )
    # No exception raised = pass


def test_spawn_memory_extraction_thread_skips_short_exchange():
    """Should not spawn thread for trivially short exchanges."""
    called = []

    class FakeClient:
        def chat(self):
            pass

    agent = SimpleNamespace(client=FakeClient(), model="test-model")

    import threading
    original_thread = threading.Thread

    class TrackingThread:
        def __init__(self, *args, **kwargs):
            called.append(True)

        def start(self):
            pass

    import agent.memory_extractor as _mod
    _mod_thread = _mod.threading.Thread
    _mod.threading.Thread = TrackingThread

    try:
        _mod.spawn_memory_extraction_thread(agent, "hi", "ok", "")
        assert not called, "Thread should not be spawned for short exchanges"
    finally:
        _mod.threading.Thread = _mod_thread
