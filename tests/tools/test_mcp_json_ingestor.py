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
    for name in ("tool_search", "tool_describe", "tool_call", "sql", "workdays", "read_file", "terminal", "memory",
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


# ---------------------------------------------------------------------------
# Window-authoritative ingest + capacity policy (AIS-275)
# ---------------------------------------------------------------------------

def _worklog(id_, day, seconds=3600, key="PROJ-1"):
    return {"id": id_, "key": key, "started": f"{day}T08:00:00Z", "timeSpentSeconds": seconds}


def _ingest(db_file, items, tool_args=None, tool_name="mcp_MyTimeMCP_getWorklogs", tool_use_id="c1"):
    return try_auto_ingest_json(
        json.dumps(items), tool_name=tool_name, tool_use_id=tool_use_id,
        db_path=db_file, tool_args=tool_args,
    )


def test_date_window_extraction_variants():
    from tools.mcp_json_ingestor import _date_window_from_args

    assert _date_window_from_args({"startDate": "2026-01-01", "endDate": "2026-01-31"}) == ("2026-01-01", "2026-01-31")
    assert _date_window_from_args({"date_from": "2026-01-01T00:00:00Z", "dateTo": "2026-02-01"}) == ("2026-01-01", "2026-02-01")
    assert _date_window_from_args({"from": "2026-01-01", "to": "2026-01-02"}) == ("2026-01-01", "2026-01-02")
    # missing one end, malformed, inverted, non-dict → None
    assert _date_window_from_args({"startDate": "2026-01-01"}) is None
    assert _date_window_from_args({"startDate": "gestern", "endDate": "2026-01-31"}) is None
    assert _date_window_from_args({"startDate": "2026-02-01", "endDate": "2026-01-01"}) is None
    assert _date_window_from_args(None) is None


def test_window_refetch_replaces_stale_rows_in_window_only(tmp_path: Path):
    db_file = tmp_path / "s.db"
    # First fetch: whole year, includes a vacation week in September.
    first = [_worklog("w1", "2026-01-05"), _worklog("v1", "2026-09-07", key="VAC-1"), _worklog("v2", "2026-09-08", key="VAC-1")]
    res = _ingest(db_file, first, tool_args={"startDate": "2026-01-01", "endDate": "2026-12-31"})
    assert res == 3 and res.replaced == 0 and res.window == ("2026-01-01", "2026-12-31")

    # The vacation moved upstream (delete + new ids). Re-fetch September only.
    second = [_worklog("v3", "2026-09-14", key="VAC-1")]
    res = _ingest(db_file, second, tool_args={"startDate": "2026-09-01", "endDate": "2026-09-30"}, tool_use_id="c2")
    assert res == 1 and res.replaced == 2  # v1+v2 dropped, September is authoritative

    conn = sqlite3.connect(str(db_file))
    days = sorted(r[0] for r in conn.execute("SELECT substr(timestamp,1,10) FROM mcp_records").fetchall())
    assert days == ["2026-01-05", "2026-09-14"]  # January survived, old September rows gone


def test_window_delete_scoped_to_same_tool(tmp_path: Path):
    db_file = tmp_path / "s.db"
    _ingest(db_file, [_worklog("a1", "2026-03-03")], tool_name="mcp_OtherMCP_getWorklogs",
            tool_args={"startDate": "2026-03-01", "endDate": "2026-03-31"})
    res = _ingest(db_file, [_worklog("b1", "2026-03-10")],
                  tool_args={"startDate": "2026-03-01", "endDate": "2026-03-31"})
    assert res == 1 and res.replaced == 0  # other tool's rows untouched
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM mcp_records").fetchone()[0] == 2


def test_empty_or_error_result_never_wipes_the_window(tmp_path: Path):
    db_file = tmp_path / "s.db"
    _ingest(db_file, [_worklog("w1", "2026-05-05")],
            tool_args={"startDate": "2026-05-01", "endDate": "2026-05-31"})
    # Empty list: parsed fine, 0 items — deliberately does NOT delete.
    res = _ingest(db_file, [], tool_args={"startDate": "2026-05-01", "endDate": "2026-05-31"}, tool_use_id="c2")
    assert res == 0
    # Error payload: same.
    err = try_auto_ingest_json(
        json.dumps({"error": "boom"}), tool_name="mcp_MyTimeMCP_getWorklogs",
        tool_use_id="c3", db_path=db_file,
        tool_args={"startDate": "2026-05-01", "endDate": "2026-05-31"},
    )
    assert err == 0
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM mcp_records").fetchone()[0] == 1


def test_prune_enforces_per_tool_and_global_caps(tmp_path: Path):
    from tools.mcp_json_ingestor import init_mcp_tables, prune_mcp_records

    db_file = tmp_path / "s.db"
    conn = sqlite3.connect(str(db_file))
    init_mcp_tables(conn)
    rows = []
    for tool, n in (("tool_a", 8), ("tool_b", 3)):
        for i in range(n):
            rows.append((f"{tool}-{i}", tool, "u", "K", f"2026-01-{i+1:02d}T08:00:00", "", 60, "", "", "{}",
                         f"2026-08-01 00:00:{i:02d}"))
    conn.executemany(
        "INSERT INTO mcp_records (id, tool_name, tool_use_id, reference_key, timestamp, user_id, "
        "duration_seconds, category, comment, raw_data, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    result = prune_mcp_records(conn, older_than_days=365, max_records=9, per_tool_max_records=5)
    # tool_a trimmed 8→5 (3 evicted); then global 8→... already ≤9 → nothing more
    assert result.cap_evicted == 3
    counts = dict(conn.execute("SELECT tool_name, COUNT(*) FROM mcp_records GROUP BY tool_name").fetchall())
    assert counts == {"tool_a": 5, "tool_b": 3}


def test_ingest_hint_reports_window_and_eviction():
    from tools.mcp_json_ingestor import IngestResult
    from tools.tool_result_storage import _build_ingest_hint

    res = IngestResult(10, replaced=4, evicted=7, window=("2026-01-01", "2026-01-31"))
    hint = _build_ingest_hint("mcp_MyTimeMCP_getWorklogs", res)
    assert "[ingested 10 rows → mcp_records" in hint
    assert "authoritative for 2026-01-01..2026-01-31" in hint
    assert "4 previously ingested rows" in hint
    assert "7 old rows" in hint and "may be incomplete" in hint


def test_graph_value_collections_become_rows(tmp_path: Path):
    """AIS-289: Microsoft Graph lists live under ``value`` — they used to be
    ingested as ONE blob row, which is why chat messages vanished behind the
    1,500-char preview."""
    db_file = tmp_path / "state.db"
    payload = json.dumps({
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#chats('x')/messages",
        "value": [
            {"id": "1788436524663", "createdDateTime": "2026-09-03T11:55:24Z", "body": {"content": "hi"}},
            {"id": "1788436524664", "createdDateTime": "2026-09-03T11:56:24Z", "body": {"content": "file"}},
        ],
    })
    count = try_auto_ingest_json(payload, tool_name="mcp_MSOffice365MCP_m365_list_chat_messages",
                                 tool_use_id="tc_graph", db_path=db_file)
    assert int(count) == 2
    rows = sqlite3.connect(str(db_file)).execute("SELECT id FROM mcp_records ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["1788436524663", "1788436524664"]
