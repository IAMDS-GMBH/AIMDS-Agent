from __future__ import annotations

from agent.conversation_loop import (
    _has_recent_successful_memory_context,
    _is_personal_context_query,
)
from agent.memory_context_audit import (
    append_memory_context_audit_event,
    read_memory_context_audit_events,
)


def test_personal_context_query_detector_matches_multilingual():
    assert _is_personal_context_query("What do you know about me?")
    assert _is_personal_context_query("Wer bin ich?")
    assert _is_personal_context_query("¿Qué sabes de mí?")


def test_personal_context_query_detector_ignores_regular_project_question():
    assert not _is_personal_context_query("How do I run the backend tests?")


def test_has_recent_successful_memory_context_uses_freshness_window():
    msgs = [
        {"role": "tool", "name": "memory_context", "content": '{"error":"x"}'},
        {"role": "tool", "name": "memory_context", "content": '{"result":"ok"}'},
    ]
    assert _has_recent_successful_memory_context(
        messages=msgs,
        tool_name="memory_context",
        freshness_turns=2,
    )


def test_memory_context_audit_roundtrip(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    append_memory_context_audit_event(
        {"status": "skip", "reason_code": "skip_recent_context_fresh", "turn_id": "t1"}
    )
    rows = read_memory_context_audit_events(limit=10, status="skip")
    assert rows
    assert rows[0]["reason_code"] == "skip_recent_context_fresh"
