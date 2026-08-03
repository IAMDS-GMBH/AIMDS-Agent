import json
import sqlite3
from pathlib import Path

from tools.mcp_json_ingestor import try_auto_ingest_json, get_db_connection


def test_auto_ingest_json_list(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    sample_list = [
        {"id": "101", "key": "IAMDS-595", "started": "2026-01-10T10:00:00Z", "author": {"displayName": "User A"}, "timeSpentSeconds": 1800, "comment": "Half day leave"},
        {"id": "102", "key": "IAMDS-595", "started": "2026-02-15T10:00:00Z", "author": {"displayName": "User A"}, "timeSpentSeconds": 3600, "comment": "Full day leave"},
    ]
    raw_json = json.dumps(sample_list)
    count = try_auto_ingest_json(raw_json, tool_name="jira_get_worklog", tool_use_id="call_1", db_path=db_file)

    assert count == 2

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, reference_key, duration_seconds, user_id, comment FROM mcp_records ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0] == ("101", "IAMDS-595", 1800, "User A", "Half day leave")
    assert rows[1] == ("102", "IAMDS-595", 3600, "User A", "Full day leave")


def test_auto_ingest_json_dict_worklogs(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    payload = {
        "worklogs": [
            {"id": "201", "issueKey": "IAMDS-100", "started": "2026-03-01", "timeSpentSeconds": 7200, "comment": "Task 1"}
        ]
    }
    count = try_auto_ingest_json(json.dumps(payload), tool_name="mcp_AtlassianMCP_jira_get_worklog", db_path=db_file)
    assert count == 1


def test_auto_ingest_untrusted_xml_wrapper(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    raw_payload = """<untrusted_tool_result source="mcp">
[
    {"id": "301", "key": "SUP-123", "summary": "Support ticket"}
]
</untrusted_tool_result>"""
    count = try_auto_ingest_json(raw_payload, tool_name="support_mcp", db_path=db_file)
    assert count == 1
