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




def test_select_with_leading_comment_and_cte_returns_rows(tmp_path: Path):
    """Agents prefix queries with `-- comments` and wrap them in CTEs; the
    result must still be the rows, not {"rows_affected": -1}."""
    db_file = tmp_path / "state.db"
    execute_sql("INSERT INTO mcp_records (id, reference_key, duration_seconds) VALUES ('r1', 'IAMDS-595', 3600)", db_path=db_file)
    execute_sql("INSERT INTO mcp_records (id, reference_key, duration_seconds) VALUES ('r2', 'EXT-95', 1800)", db_path=db_file)

    res = execute_sql(
        "\n-- Schritt 1: Stunden je Vorgang\n"
        "WITH t AS (SELECT reference_key, duration_seconds FROM mcp_records)\n"
        "SELECT reference_key, ROUND(SUM(duration_seconds)/3600.0, 2) AS hours FROM t GROUP BY reference_key ORDER BY 1;",
        db_path=db_file,
    )
    assert "rows_affected" not in res
    assert "| EXT-95 | 0.5 |" in res and "| IAMDS-595 | 1.0 |" in res
    assert "2 rows returned" in res


def test_write_with_leading_comment_reports_rows_affected(tmp_path: Path):
    db_file = tmp_path / "state.db"
    res = execute_sql("-- add one\nINSERT INTO mcp_records (id, reference_key) VALUES ('x', 'A-1')", db_path=db_file)
    assert json.loads(res) == {"status": "success", "rows_affected": 1}
    assert "1 rows returned" in execute_sql("/* read */ SELECT id FROM mcp_records", db_path=db_file)


def test_default_db_path_follows_hermes_home_at_call_time(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    execute_sql("INSERT INTO mcp_records (id, reference_key) VALUES ('h', 'A-1')")
    assert (tmp_path / "home" / "state.db").exists()
