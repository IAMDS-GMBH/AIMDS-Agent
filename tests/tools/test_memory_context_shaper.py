"""AIS-740: memory_context results are bounded and a repeat is deduplicated."""

from __future__ import annotations

import json

from tools.memory_context_shaper import (
    BOUNDED_KEY,
    UNCHANGED_KEY,
    bound_memory_context_result,
    dedupe_memory_context_message,
    is_memory_context_tool,
    is_unchanged_stub,
)

TOOL = "mcp_AIMDSSuiteMCP_mcp_memory_memory_context"


def _briefing(rules: int = 20, rule_chars: int = 800, memories: int = 16) -> dict:
    return {
        "rules": [
            {
                "content": f"Rule {i}: " + ("x" * rule_chars),
                "priority": 10,
                "slug": f"rule-{i}",
                "tags": ["always"],
                "title": f"Rule {i}",
                "truncated": True,
            }
            for i in range(rules)
        ],
        "profile": [{"content": "Johannes, developer", "slug": "profile", "title": "Profile"}],
        "memories": [
            {"content_hash": "abcd1234", "preview": "m" * 80, "slug": f"mem-{i}", "title": f"Memory {i}"}
            for i in range(memories)
        ],
        "workflow": {"contexts": "coding, git", "dev": "x"},
        "context_hint": "No active_contexts set.",
        "context_bytes": 24899,
    }


def _wrapped(body: dict) -> str:
    # The Suite returns the briefing JSON-encoded inside {"result": "..."}.
    return json.dumps({"result": json.dumps(body)})


def test_tool_name_match():
    assert is_memory_context_tool(TOOL)
    assert is_memory_context_tool("memory_context")
    assert not is_memory_context_tool("mcp_AIMDSSuiteMCP_mcp_memory_memory_search")


def test_large_briefing_is_bounded_and_keeps_its_structure():
    raw = _wrapped(_briefing())
    out = bound_memory_context_result(raw, max_chars=12_000)
    assert len(raw) > 20_000
    assert len(out) <= 12_000
    data = json.loads(out)
    # The double encoding is gone: result is an object, not an escaped string.
    body = data["result"]
    assert isinstance(body, dict)
    # Every top-level field survives.
    for key in ("rules", "profile", "memories", "workflow", "context_hint", "context_bytes"):
        assert key in body
    assert BOUNDED_KEY in body
    # Shortened entries still name themselves so the model can read them in full.
    assert all(r["slug"].startswith("rule-") and r["title"] for r in body["rules"])
    # Hash keys are dropped.
    assert all("content_hash" not in m for m in body["memories"])


def test_bounding_is_deterministic():
    raw = _wrapped(_briefing())
    assert bound_memory_context_result(raw, max_chars=12_000) == bound_memory_context_result(raw, max_chars=12_000)


def test_texts_shrink_before_entries_are_dropped():
    out = json.loads(bound_memory_context_result(_wrapped(_briefing()), max_chars=20_000))["result"]
    assert len(out["rules"]) == 20
    assert out[BOUNDED_KEY]  # a note, no omitted entries
    assert "omitted_items" not in (out[BOUNDED_KEY] if isinstance(out[BOUNDED_KEY], dict) else {})


def test_small_briefing_only_loses_its_escaping():
    body = _briefing(rules=2, rule_chars=50, memories=2)
    out = bound_memory_context_result(_wrapped(body), max_chars=20_000)
    data = json.loads(out)["result"]
    assert BOUNDED_KEY not in data
    assert data["rules"] == body["rules"]


def test_onboarding_fields_are_never_shaped():
    body = _briefing()
    body["onboarding_questions"] = [f"Question {i}? " + "q" * 400 for i in range(10)]
    body["onboarding_question_flow_required"] = True
    out = json.loads(bound_memory_context_result(_wrapped(body), max_chars=8_000))["result"]
    assert out["onboarding_questions"] == body["onboarding_questions"]
    assert out["onboarding_question_flow_required"] is True


def test_errors_and_plain_text_pass_through():
    err = json.dumps({"error": "boom " * 10_000})
    assert bound_memory_context_result(err, max_chars=100) == err
    text = "plain " * 10_000
    assert bound_memory_context_result(text, max_chars=100) == text


def test_zero_limit_disables():
    raw = _wrapped(_briefing())
    assert bound_memory_context_result(raw, max_chars=0) == raw


def _tool_msg(content: str, call_id: str) -> dict:
    return {"role": "tool", "name": TOOL, "tool_call_id": call_id, "content": content}


def test_repeated_briefing_becomes_a_reference():
    content = bound_memory_context_result(_wrapped(_briefing()), max_chars=12_000)
    wrapped = f'<untrusted_tool_result source="{TOOL}">\nData.\n\n{content}\n</untrusted_tool_result>'
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        _tool_msg(wrapped, "a"),
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]},
        _tool_msg(wrapped, "b"),
    ]
    assert dedupe_memory_context_message(messages) is True
    assert is_unchanged_stub(messages[-1]["content"])
    assert json.loads(messages[-1]["content"])[UNCHANGED_KEY] is True
    assert len(messages[-1]["content"]) < 200
    # The first load stays intact, and a second pass is a no-op.
    assert messages[2]["content"] == wrapped
    assert dedupe_memory_context_message(messages) is False


def test_changed_briefing_is_kept():
    first = bound_memory_context_result(_wrapped(_briefing(rules=3)), max_chars=12_000)
    second = bound_memory_context_result(_wrapped(_briefing(rules=4)), max_chars=12_000)
    messages = [_tool_msg(first, "a"), {"role": "user", "content": "x"}, _tool_msg(second, "b")]
    assert dedupe_memory_context_message(messages) is False
    assert messages[-1]["content"] == second


def test_onboarding_reader_skips_the_reference():
    from agent.conversation_loop import _read_recent_memory_context_payload

    payload = {"onboarding_question_flow_required": True, "onboarding_first_question": "Role?"}
    first = json.dumps(payload)
    messages = [
        _tool_msg(first, "a"),
        _tool_msg(json.dumps({UNCHANGED_KEY: True, "note": "same"}), "b"),
    ]
    got = _read_recent_memory_context_payload(messages=messages, valid_tool_names={TOOL})
    assert got == payload


def test_tool_result_pipeline_bounds_memory_context(monkeypatch):
    from tools.tool_result_storage import maybe_persist_tool_result

    monkeypatch.setattr("tools.memory_context_shaper.load_max_chars", lambda: 12_000)
    raw = _wrapped(_briefing())
    out = maybe_persist_tool_result(content=raw, tool_name=TOOL, tool_use_id="t1", env=None)
    assert len(out) <= 12_000
    assert isinstance(json.loads(out)["result"], dict)
    # Other memory tools are not shaped by this (memory_read stays uncapped).
    other = maybe_persist_tool_result(
        content=raw, tool_name="mcp_AIMDSSuiteMCP_mcp_memory_memory_read", tool_use_id="t2", env=None,
    )
    assert other == raw
