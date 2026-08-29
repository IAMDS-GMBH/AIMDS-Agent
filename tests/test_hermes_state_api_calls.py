"""hermes_state.api_calls — one row per LLM request (served model, cache accounting)."""

from __future__ import annotations

import time

from hermes_state import SessionDB


def _db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


def test_record_and_read_back_in_order(tmp_path):
    db = _db(tmp_path)
    db.create_session("s1", source="tui")
    db.record_api_call("s1", requested_model="AIMDS-Suite-Auto", served_model="claude-haiku-4.5", provider="aimds-suite-prod",
                       input_tokens=20_000, cache_read_tokens=0, cache_write_tokens=18_000, output_tokens=300, prompt_tokens=38_000,
                       tools_count=25, breakpoints=4, latency_ms=2100)
    db.record_api_call("s1", requested_model="AIMDS-Suite-Auto", served_model="claude-haiku-4.5", provider="aimds-suite-prod",
                       input_tokens=1_200, cache_read_tokens=38_000, cache_write_tokens=900, output_tokens=200, prompt_tokens=40_100)
    rows = db.get_api_calls(session_id="s1")
    assert [r["cache_read_tokens"] for r in rows] == [0, 38_000]
    assert rows[0]["served_model"] == "claude-haiku-4.5" and rows[0]["breakpoints"] == 4 and rows[0]["latency_ms"] == 2100
    assert db.get_api_calls(session_id="other") == []
    db.close()


def test_retention_prunes_old_rows_and_window_filter(tmp_path, monkeypatch):
    db = _db(tmp_path)
    db.create_session("s1", source="tui")
    old = time.time() - 40 * 86400
    monkeypatch.setattr("hermes_state.time.time", lambda: old)
    db.record_api_call("s1", input_tokens=1)
    monkeypatch.undo()
    db.record_api_call("s1", input_tokens=2)  # prunes the 40-day-old row
    rows = db.get_api_calls()
    assert [r["input_tokens"] for r in rows] == [2]
    assert db.get_api_calls(days=1) == rows
    db.close()


def test_empty_session_id_is_ignored(tmp_path):
    db = _db(tmp_path)
    db.record_api_call("", input_tokens=5)
    assert db.get_api_calls() == []
    db.close()


def test_table_is_created_on_an_existing_database(tmp_path):
    """SCHEMA_SQL runs CREATE TABLE IF NOT EXISTS on every open — an older
    state.db gains api_calls without a manual migration."""
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    db.close()
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE api_calls")
    conn.commit(); conn.close()
    db = SessionDB(db_path=path)
    names = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "api_calls" in names
    db.close()
