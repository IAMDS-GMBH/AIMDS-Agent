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


def test_auto_ingest_nested_jira_result_string(tmp_path: Path):
    db_file = tmp_path / "test_state.db"
    nested_worklogs = {
        "worklogs": [
            {"id": "57171", "comment": "time-tracking", "created": "2025-09-19", "timeSpentSeconds": 1800, "issue": {"key": "IAMDS-595"}},
            {"id": "57172", "comment": "vacation", "created": "2025-09-20", "timeSpentSeconds": 3600, "issue": {"key": "IAMDS-595"}}
        ]
    }
    mcp_wrapper = {
        "result": json.dumps(nested_worklogs)
    }
    raw_payload = f"""<untrusted_tool_result source="mcp_AtlassianMCP_jira_get_worklog">
{json.dumps(mcp_wrapper)}
</untrusted_tool_result>"""

    count = try_auto_ingest_json(raw_payload, tool_name="mcp_AtlassianMCP_jira_get_worklog", db_path=db_file)
    assert count == 2

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, reference_key, duration_seconds FROM mcp_records ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0] == ("57171", "IAMDS-595", 1800)
    assert rows[1] == ("57172", "IAMDS-595", 3600)


def test_only_data_tools_are_ingested():
    from tools.mcp_json_ingestor import should_ingest_tool
    for name in ("tool_search", "tool_describe", "tool_call", "sql", "read_file", "terminal", "memory",
                 "mcp_AIMDSSuiteMCP_mcp_memory_memory_context", "mcp_AIMDSSuiteMCP_mcp_memory_skill",
                 "mcp_AIMDSSuiteMCP_kb_search", "mcp_AtlassianMCP_list_resources"):
        assert should_ingest_tool(name) is False, name
    for name in ("mcp_AtlassianMCP_jira_search", "mcp_AtlassianMCP_jira_get_worklog", "mcp_TempoMCP_retrieveWorklogs",
                 "jira_get_worklog", "support_mcp", "mcp"):
        assert should_ingest_tool(name) is True, name


def test_bridge_and_error_payloads_produce_no_rows(tmp_path: Path):
    db_file = tmp_path / "state.db"
    search_result = json.dumps({"query": "x", "matches": [{"name": "a", "kind": "tool"}], "autoload": []})
    assert try_auto_ingest_json(search_result, tool_name="tool_search", db_path=db_file) == 0
    err = json.dumps({"error": "1 validation error for call[search]"})
    assert try_auto_ingest_json(err, tool_name="mcp_AtlassianMCP_jira_search", db_path=db_file) == 0
    wrapped_err = json.dumps({"result": json.dumps({"error": "boom"})})
    assert try_auto_ingest_json(wrapped_err, tool_name="mcp_AtlassianMCP_jira_get_worklog", db_path=db_file) == 0


def test_nested_worklogs_of_a_jira_search_become_rows_with_the_issue_key(tmp_path: Path):
    db_file = tmp_path / "state.db"
    payload = {"total": -1, "issues": [
        {"id": "1", "key": "EXT-95", "summary": "EVN Daily", "status": {"name": "In Arbeit"},
         "worklog": {"startAt": 0, "maxResults": 20, "total": 2, "worklogs": [
             {"id": "w1", "started": "2026-08-03T08:00:00.000+0200", "timeSpentSeconds": 3600,
              "author": {"displayName": "Johannes"}, "comment": "IAMDS EVN Daily"},
             {"id": "w2", "started": "2026-08-04T08:00:00.000+0200", "time_spent_seconds": 1800,
              "author": {"displayName": "Johannes"}},
         ]}},
    ]}
    count = try_auto_ingest_json(json.dumps({"result": json.dumps(payload)}), tool_name="mcp_AtlassianMCP_jira_search", db_path=db_file)
    assert count == 3
    rows = sqlite3.connect(str(db_file)).execute(
        "SELECT reference_key, category, duration_seconds FROM mcp_records ORDER BY id").fetchall()
    assert ("EXT-95", "worklog", 3600) in rows and ("EXT-95", "worklog", 1800) in rows
    assert ("EXT-95", "In Arbeit", 0) in rows
    total = sqlite3.connect(str(db_file)).execute(
        "SELECT SUM(duration_seconds) FROM mcp_records WHERE reference_key='EXT-95' AND category='worklog'").fetchone()[0]
    assert total == 5400


def test_per_issue_tools_take_the_issue_key_from_the_request(tmp_path: Path):
    """jira_get_worklog replies carry no issue key — 625 rows with an empty
    reference_key made GROUP BY reference_key meaningless in a real session."""
    db_file = tmp_path / "state.db"
    payload = {"worklogs": [{"id": "9", "started": "2026-08-01", "timeSpent": "1h 30m"}]}
    try_auto_ingest_json(json.dumps(payload), tool_name="mcp_AtlassianMCP_jira_get_worklog", db_path=db_file,
                         tool_args={"issue_key": "IAMDS-595"})
    row = sqlite3.connect(str(db_file)).execute("SELECT reference_key, duration_seconds FROM mcp_records").fetchone()
    assert row == ("IAMDS-595", 5400)
