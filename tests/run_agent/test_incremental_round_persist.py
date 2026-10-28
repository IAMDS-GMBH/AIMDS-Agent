"""AIS-736: turn progress is stored per tool round, atomically.

A turn used to reach state.db only at its exit. An app restart in the
middle of a long turn lost every finished round, and a later flush could
store tool results without the assistant message that called them. These
tests pin the round-by-round persistence, the compaction-continuation write
at rotation time and the no-duplicate guarantee at the normal turn end.
"""

import json
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB


def _rows(db: SessionDB, session_id: str):
    return db.get_messages_as_conversation(session_id)


def _assert_no_orphan_tool_results(rows):
    known = set()
    for row in rows:
        if row["role"] == "assistant":
            known = {tc.get("id") for tc in (row.get("tool_calls") or [])}
        elif row["role"] == "tool":
            assert row.get("tool_call_id") in known, f"orphan tool result: {row}"
        else:
            known = set()


def _make_agent(db, session_id="ais736"):
    from run_agent import AIAgent

    agent = AIAgent(
        model="test-model",
        api_key="test-key",
        base_url="http://localhost:8080/v1",
        platform="cli",
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        session_db=db,
        session_id=session_id,
    )
    agent._ensure_db_session()
    return agent


def _round(n: int, calls: int = 1):
    ids = [f"call_{n}_{i}" for i in range(calls)]
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            for cid in ids
        ],
    }
    results = [
        {"role": "tool", "tool_call_id": cid, "tool_name": "read_file", "content": f"result {cid}"}
        for cid in ids
    ]
    return assistant, results


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


class TestRoundPersistence:
    def test_turn_killed_after_n_rounds_leaves_n_complete_rounds(self, db):
        agent = _make_agent(db)
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
        db.append_messages(agent.session_id, [dict(role=m["role"], content=m["content"]) for m in history])
        agent._last_flushed_db_idx = len(history)

        messages = list(history) + [{"role": "user", "content": "do three things"}]
        agent._persist_session(messages, history)  # turn-start persist

        for n in range(2):
            assistant, results = _round(n, calls=2)
            messages.append(assistant)
            messages.extend(results)
            agent._persist_tool_round(messages, history)

        # Round 3 started: its assistant message is appended, a tool runs …
        assistant, results = _round(2, calls=2)
        messages.append(assistant)
        messages.append(results[0])
        agent._persist_tool_round(messages, history)  # incomplete — must not write
        # … and the process dies here: no turn-exit persist.

        rows = _rows(db, agent.session_id)
        assert [r["role"] for r in rows] == [
            "user", "assistant", "user",
            "assistant", "tool", "tool",
            "assistant", "tool", "tool",
        ]
        _assert_no_orphan_tool_results(rows)

    def test_one_transaction_per_round(self, db, monkeypatch):
        agent = _make_agent(db)
        messages = [{"role": "user", "content": "go"}]
        agent._persist_session(messages, [])

        writes = []
        real = db._execute_write
        monkeypatch.setattr(db, "_execute_write", lambda fn: (writes.append(fn), real(fn))[1])

        assistant, results = _round(0, calls=3)
        messages.append(assistant)
        messages.extend(results)
        agent._persist_tool_round(messages, [])

        assert len(writes) == 1
        assert len(_rows(db, agent.session_id)) == 5

    def test_normal_turn_end_writes_no_duplicates(self, db):
        agent = _make_agent(db)
        messages = [{"role": "user", "content": "go"}]
        agent._persist_session(messages, [])
        for n in range(2):
            assistant, results = _round(n)
            messages.append(assistant)
            messages.extend(results)
            agent._persist_tool_round(messages, [])
        messages.append({"role": "assistant", "content": "done"})
        agent._persist_session(messages, [])
        agent._persist_session(messages, [])  # a second exit path

        rows = _rows(db, agent.session_id)
        assert len(rows) == len(messages)
        assert db.get_session(agent.session_id)["message_count"] == len(messages)

    def test_compaction_continuation_has_its_transcript_at_rotation(self, db):
        agent = _make_agent(db)
        messages = [{"role": "user", "content": "go"}]
        agent._persist_session(messages, [])

        # What _compress_context does on rotation: end the parent, open the
        # continuation, reset the flush cursor.
        db.end_session(agent.session_id, "compression")
        parent = agent.session_id
        agent.session_id = "ais736-cont"
        db.create_session(session_id=agent.session_id, source="cli", parent_session_id=parent)
        agent._last_flushed_db_idx = 0
        compressed = [
            {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
            {"role": "user", "content": "go"},
        ]
        agent._persist_compaction_rotation(compressed)

        assert [r["content"] for r in _rows(db, agent.session_id)] == [
            "[CONTEXT COMPACTION] summary",
            "go",
        ]

        # The rest of the turn appends; nothing is written twice.
        assistant, results = _round(0)
        compressed.append(assistant)
        compressed.extend(results)
        agent._persist_tool_round(compressed, None)
        compressed.append({"role": "assistant", "content": "done"})
        agent._persist_session(compressed, None)
        assert len(_rows(db, agent.session_id)) == len(compressed)

    def test_repaired_user_merge_does_not_drop_the_next_assistant(self, db):
        """The incident shape: the killed turn's user message is the history
        tail, the next turn's user message is merged into it before the API
        call. The flush cursor must follow, or the assistant that issues the
        next tool round is skipped and its results are stored orphaned."""
        from agent.agent_runtime_helpers import repair_message_sequence

        agent = _make_agent(db)
        history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "the killed turn"},
        ]
        db.append_messages(agent.session_id, [dict(role=m["role"], content=m["content"]) for m in history])
        agent._last_flushed_db_idx = len(history)

        messages = [dict(m) for m in history] + [{"role": "user", "content": "are you still there?"}]
        agent._persist_session(messages, history)
        assert repair_message_sequence(agent, messages) == 1
        assert agent._last_flushed_db_idx == len(messages)

        assistant, results = _round(0)
        messages.append(assistant)
        messages.extend(results)
        agent._persist_tool_round(messages, history)

        rows = _rows(db, agent.session_id)
        assert [r["role"] for r in rows][-2:] == ["assistant", "tool"]
        _assert_no_orphan_tool_results(rows)


def test_run_conversation_stores_each_round_before_the_next_call(tmp_path, monkeypatch):
    """End to end through the loop: when the third model call starts, the
    two finished rounds are already in state.db — a restart at that point
    keeps them. The normal turn end then adds only the answer."""
    from run_agent import AIAgent

    db = SessionDB(db_path=tmp_path / "state.db")
    seen_at_third_call = {}

    def _tool_response(n):
        call = SimpleNamespace(
            id=f"call_{n}", type="function",
            function=SimpleNamespace(name="read_file", arguments=json.dumps({"path": f"f{n}"})),
        )
        message = SimpleNamespace(content=None, reasoning=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None)

    class _Completions:
        calls = 0

        def create(self, **_kwargs):
            _Completions.calls += 1
            if _Completions.calls <= 2:
                return _tool_response(_Completions.calls)
            seen_at_third_call["rows"] = db.get_messages_as_conversation("ais736-e2e")
            message = SimpleNamespace(content="done", reasoning=None, tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr("run_agent.OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(
        "run_agent.get_tool_definitions",
        lambda *args, **kwargs: [{"function": {"name": "read_file"}}],
    )
    monkeypatch.setattr(
        "run_agent.handle_function_call",
        lambda name, args, task_id=None, **kwargs: json.dumps({"ok": True}),
    )
    agent = AIAgent(
        model="test-model",
        api_key="test-key",
        base_url="http://localhost:8080/v1",
        platform="cli",
        max_iterations=5,
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        session_db=db,
        session_id="ais736-e2e",
    )
    agent._disable_streaming = True

    try:
        agent.run_conversation("read two files")
        rows = seen_at_third_call["rows"]
        assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant", "tool"]
        _assert_no_orphan_tool_results(rows)
        final = db.get_messages_as_conversation("ais736-e2e")
        assert [r["role"] for r in final] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    finally:
        db.close()


class TestCutOffNote:
    def test_history_ending_in_tool_results_gets_a_note(self):
        from agent.turn_context import _previous_turn_cut_off

        assistant, results = _round(0)
        assert _previous_turn_cut_off([{"role": "user", "content": "x"}, assistant, *results])
        assert not _previous_turn_cut_off([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
        assert not _previous_turn_cut_off([])
