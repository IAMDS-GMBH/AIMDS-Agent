"""hermes_state.agent_runs — one row per subagent run (AIS-796), and the usage
counters the client telemetry sends. Synthetic sessions only."""

from __future__ import annotations

import sqlite3
import time

from hermes_state import SessionDB


def _db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


def test_record_and_read_back(tmp_path):
    db = _db(tmp_path)
    db.create_session("chat-1", source="tui")
    db.record_agent_run(
        "chat-1", child_session_id="child-1", definition="apply", definition_digest="abc123def456",
        goal_hash="0f1e2d3c4b5a6978", duration_ms=42_000, iterations=9, tool_calls=31, tool_errors=1,
        result_chars=1_800, status="completed",
    )
    db.record_agent_run("chat-1", definition="collect", status="failed")
    rows = db.get_agent_runs(session_id="chat-1")
    assert [r["definition"] for r in rows] == ["apply", "collect"]
    assert rows[0]["tool_calls"] == 31 and rows[0]["iterations"] == 9 and rows[0]["status"] == "completed"
    assert rows[0]["goal_hash"] == "0f1e2d3c4b5a6978" and rows[0]["child_session_id"] == "child-1"
    # No column can hold a goal or a result text.
    columns = {r[1] for r in db._conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
    assert not columns & {"goal", "summary", "result", "context"}
    assert db.get_agent_runs(session_id="other") == []
    db.close()


def test_empty_session_and_retention(tmp_path, monkeypatch):
    db = _db(tmp_path)
    db.record_agent_run("", definition="apply")
    assert db.get_agent_runs() == []
    old = time.time() - (SessionDB.AGENT_RUNS_RETENTION_DAYS + 5) * 86400
    monkeypatch.setattr("hermes_state.time.time", lambda: old)
    db.record_agent_run("chat-1", definition="apply")
    monkeypatch.undo()
    db.record_agent_run("chat-1", definition="sweep")  # prunes the old row
    assert [r["definition"] for r in db.get_agent_runs()] == ["sweep"]
    assert db.get_agent_runs(days=1)[0]["definition"] == "sweep"
    db.close()


def test_table_is_created_on_an_existing_database(tmp_path):
    path = tmp_path / "state.db"
    SessionDB(db_path=path).close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE agent_runs")
    conn.commit()
    conn.close()
    db = SessionDB(db_path=path)
    db.record_agent_run("chat-1", definition="apply", tool_calls=3)
    assert db.get_agent_runs()[0]["tool_calls"] == 3
    db.close()


def test_usage_counters_split_chat_and_child_calls_and_rate_compressions(tmp_path, monkeypatch):
    db = _db(tmp_path)
    clock = [time.time() - 3 * 3600]
    monkeypatch.setattr("hermes_state.time.time", lambda: clock[0])

    def tool_calls(session_id, count):
        calls = [{"id": f"t{i}", "type": "function", "function": {"name": "x", "arguments": "{}"}} for i in range(count)]
        db.append_message(session_id, "assistant", tool_calls=calls)

    # A chat compressed twice within one hour: chat-a → chat-b → chat-c.
    db.create_session("chat-a", source="tui")
    tool_calls("chat-a", 40)
    clock[0] += 1800
    db.end_session("chat-a", "compression")
    db.create_session("chat-b", source="tui", parent_session_id="chat-a")
    tool_calls("chat-b", 30)
    clock[0] += 1800
    db.end_session("chat-b", "compression")
    db.create_session("chat-c", source="tui", parent_session_id="chat-b")
    tool_calls("chat-c", 2)
    # Its subagent run: 31 tool calls in the child session.
    db.create_session("child-1", source="tui", parent_session_id="chat-c")
    tool_calls("child-1", 31)
    db.record_agent_run("chat-c", child_session_id="child-1", definition="apply", tool_calls=31, status="completed")
    # Another chat, no compression.
    db.create_session("chat-x", source="tui")
    tool_calls("chat-x", 5)
    monkeypatch.undo()

    counters = db.agent_usage_counters(days=7)
    assert counters["chats"] == 2
    assert counters["tool_calls_main"] == 40 + 30 + 2 + 5
    assert counters["tool_calls_child"] == 31
    assert counters["agent_runs"] == 1 and counters["agent_runs_by_definition"] == {"apply": 1}
    assert counters["compressions"] == 2
    assert counters["compressions_per_hour"] == {"chats": 1, "median": 2.0, "max": 2.0}
    # Counters only: numbers and definition names, nothing from the chats.
    flat = repr(counters)
    assert "chat-" not in flat and "child-1" not in flat
    db.close()


def test_usage_counters_on_an_empty_database(tmp_path):
    db = _db(tmp_path)
    counters = db.agent_usage_counters()
    assert counters["chats"] == 0 and counters["agent_runs"] == 0
    assert counters["compressions_per_hour"] == {"chats": 0, "median": 0.0, "max": 0.0}
    db.close()
