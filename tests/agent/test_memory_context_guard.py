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


def test_personal_context_query_detector_known_limit_dialect():
    """Documents a known, accepted gap: a hardcoded phrase list can't cover
    every dialect/phrasing. Support cases SUP-20260918-094851/094912's exact
    text (Bavarian) matches neither the seed regexes nor the space-bounded
    fallback ("bin i" != "bin ich", "woast" != "weißt", "mi?" has no
    trailing space for the " mi " fallback token). Coverage for this case
    comes from the memory-vault prompt guidance instead (agent/prompt_builder.py
    build_memory_vault_guidance) — the model understands intent regardless
    of dialect even where this regex backstop cannot."""
    assert not _is_personal_context_query("Wer bin i und wos woast du üba mi? List oise auf")


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
