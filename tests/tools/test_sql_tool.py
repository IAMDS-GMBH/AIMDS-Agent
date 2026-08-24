import json
import sqlite3
from pathlib import Path

from tools.sql_tool import execute_sql


def test_sql_tool_select_and_insert(tmp_path: Path):
    db_file = tmp_path / "test_state.db"

    # Insert test row
    res_insert = execute_sql("INSERT INTO mcp_records (id, reference_key, duration_seconds) VALUES ('r1', 'IAMDS-595', 3600)", db_path=db_file)
    assert "rows_affected" in res_insert

    # Select query
    res_select = execute_sql("SELECT id, reference_key, duration_seconds FROM mcp_records WHERE reference_key = 'IAMDS-595'", db_path=db_file)
    assert "| r1 | IAMDS-595 | 3600 |" in res_select
    assert "1 rows returned" in res_select


def test_sql_tool_read_only_protection(tmp_path: Path):
    db_file = tmp_path / "test_state.db"

    # Create dummy sessions table
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
    conn.close()

    # SELECT should work
    res_select = execute_sql("SELECT * FROM sessions", db_path=db_file)
    assert '"count": 0' in res_select

    # INSERT into sessions should be DENIED
    res_insert = execute_sql("INSERT INTO sessions (id, title) VALUES ('s1', 'Test')", db_path=db_file)
    assert "Modification denied" in res_insert

    # UPDATE sessions should be DENIED
    res_update = execute_sql("UPDATE sessions SET title='Hacked'", db_path=db_file)
    assert "Modification denied" in res_update

    # DELETE from sessions should be DENIED
    res_delete = execute_sql("DELETE FROM sessions", db_path=db_file)
    assert "Modification denied" in res_delete

    # DROP TABLE sessions should be DENIED
    res_drop = execute_sql("DROP TABLE sessions", db_path=db_file)
    assert "Modification denied" in res_drop


def test_sql_tool_handle_arg_variations(tmp_path: Path):
    from tools.sql_tool import _handle_sql
    db_file = tmp_path / "test_state.db"

    res_stmt = _handle_sql({"statement": "SELECT 1 as val"})
    assert "val" in res_stmt or "rows returned" in res_stmt

    res_sql = _handle_sql({"sql": "SELECT 2 as val"})
    assert "val" in res_sql or "rows returned" in res_sql


def test_mcp_records_pruning(tmp_path: Path):
    from tools.mcp_json_ingestor import prune_mcp_records, get_db_connection
    db_file = tmp_path / "test_state.db"
    conn = get_db_connection(db_file)
    with conn:
        conn.execute("INSERT INTO mcp_records (id, created_at) VALUES ('old1', datetime('now', '-30 days'))")
        conn.execute("INSERT INTO mcp_records (id, created_at) VALUES ('new1', datetime('now'))")

    pruned = prune_mcp_records(conn, older_than_days=14)
    assert pruned == 1

    cursor = conn.execute("SELECT id FROM mcp_records")
    ids = [r[0] for r in cursor.fetchall()]
    assert "old1" not in ids
    assert "new1" in ids
    conn.close()


