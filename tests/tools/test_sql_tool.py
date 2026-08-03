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


def test_sql_tool_error_handling(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    res_err = execute_sql("SELECT * FROM non_existent_table", db_path=db_file)
    assert "SQLite error:" in res_err
