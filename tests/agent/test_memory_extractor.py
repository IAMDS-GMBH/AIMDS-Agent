"""Tests for agent/memory_extractor.py — LLM-assisted memory extraction."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent.memory_extractor import (
    _looks_natural_language,
    _run_extraction,
    _build_extraction_messages,
    _parse_extraction_response,
    read_extraction_audit_events,
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
        {"title": "Language", "content": "User prefers Spanish.", "type": "profile", "scope": "user", "tags": ["language"], "confidence": 0.91},
    ])
    facts = _parse_extraction_response(payload)
    assert len(facts) == 1
    assert facts[0]["title"] == "Language"
    assert facts[0]["scope"] == "user"
    assert facts[0]["type"] == "profile"
    assert facts[0]["confidence"] == 0.91


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


def test_parse_extraction_response_confidence_clamped():
    payload = json.dumps([
        {"title": "A", "content": "B", "type": "notes", "scope": "project", "tags": [], "confidence": 9.4},
    ])
    facts = _parse_extraction_response(payload)
    assert len(facts) == 1
    assert facts[0]["confidence"] == 1.0


def test_should_attempt_extraction_short_text():
    assert not should_attempt_extraction("hi", "ok")


def test_should_attempt_extraction_short_user_fact_statement():
    assert should_attempt_extraction(
        "I work mostly on backend APIs in this repo.",
        "Got it.",
    )


def test_looks_natural_language_for_semantic_text():
    assert _looks_natural_language("I work mostly on backend APIs in this repo.")


def test_looks_natural_language_rejects_short_noise():
    assert not _looks_natural_language("ok")


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


def test_should_attempt_extraction_non_english_semantic_text():
    assert should_attempt_extraction(
        "Ich arbeite in diesem Repo hauptsaechlich an Backend-APIs.",
        "Verstanden.",
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


def test_spawn_memory_extraction_thread_writes_skip_prefilter_audit(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    bad_agent = SimpleNamespace(session_id="s1", model="m1")
    spawn_memory_extraction_thread(bad_agent, "hi", "ok", effective_task_id="t1")

    rows = read_extraction_audit_events(limit=5, status="skip")
    assert rows
    assert rows[0]["reason_code"] == "skip_prefilter"


def test_run_extraction_writes_save_audit_with_confidence(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    class _Msg:
        content = '[{"title":"Backend focus","content":"User works on backend APIs.","type":"project","scope":"project","tags":["backend"],"confidence":0.8}]'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    agent = SimpleNamespace(client=_Client(), model="m1", session_id="s1", _current_turn_id="turn-1")

    import agent.memory_dual_write as _dual
    monkeypatch.setattr(_dual, "build_structured_mirror_record", lambda **kwargs: {"id": "1"})
    monkeypatch.setattr(_dual, "upsert_structured_mirror_record", lambda record: None)

    _run_extraction(
        agent,
        user_message="I work mostly on backend APIs in this repo.",
        assistant_message="Got it.",
        effective_task_id="t1",
    )

    saves = read_extraction_audit_events(limit=10, status="save")
    assert saves
    assert saves[0]["reason_code"] == "save_facts_written"
    assert abs(float(saves[0]["confidence"]) - 0.8) < 1e-6
