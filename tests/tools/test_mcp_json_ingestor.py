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


def test_openproject_time_entries_map_like_tempo_worklogs(tmp_path: Path):
    """AIS-327: openproject-ce-mcp `list_time_entries` rows carry `spent_on`,
    `work_package_id` and `hours` as an ISO 8601 duration — the same kind of
    time tracking as Jira + Tempo, so the `workdays` report must be able to
    sum them from mcp_records."""
    db_file = tmp_path / "state.db"
    payload = json.dumps({
        "offset": 1, "limit": 10, "total": 2, "next_offset": None,
        "results": [
            {"id": 501, "hours": "PT1H30M", "spent_on": "2026-09-14", "comment": "review",
             "activity": "Development", "user": "Johannes Huchler", "work_package_id": 17},
            {"id": 502, "hours": "PT8H", "spent_on": "2026-09-15", "comment": "",
             "activity": "Development", "user": "Johannes Huchler", "work_package_id": "WSA-3"},
        ],
    })
    count = try_auto_ingest_json(payload, tool_name="mcp_op_list_time_entries",
                                 tool_use_id="tc_op", db_path=db_file)
    assert int(count) == 2
    rows = sqlite3.connect(str(db_file)).execute(
        "SELECT id, reference_key, timestamp, duration_seconds, user_id FROM mcp_records ORDER BY id"
    ).fetchall()
    assert rows == [
        ("501", "17", "2026-09-14", 5400, "Johannes Huchler"),
        ("502", "WSA-3", "2026-09-15", 28800, "Johannes Huchler"),
    ]


def test_graph_calendar_events_get_timestamp_duration_and_calendar(tmp_path: Path):
    """AIS-339 (session 20260915_082908): m365_get_events rows landed with an
    empty timestamp, no duration and no reference — a per-day JOIN against
    workday_calendar was impossible. Graph events carry ``start``/``end``
    dicts plus the server's ``start_iso_local``; the calendar name is
    stamped per event (or taken from the ``calendar`` argument)."""
    db_file = tmp_path / "state.db"
    payload = json.dumps({
        "resolved_calendar_name": "OFFICEZEITEN",
        "value": [
            {"id": "evt-1", "subject": "Johannes Huchler", "isAllDay": False, "categories": [],
             "start": {"dateTime": "2026-09-01T08:00:00.0000000", "timeZone": "Europe/Berlin"},
             "end": {"dateTime": "2026-09-01T17:00:00.0000000", "timeZone": "Europe/Berlin"},
             "organizer": {"emailAddress": {"name": "Johannes Huchler", "address": "j@x.de"}},
             "start_iso_local": "2026-09-01T08:00:00", "end_iso_local": "2026-09-01T17:00:00",
             "calendar_name": "OFFICEZEITEN"},
            {"id": "evt-2", "subject": "Urlaub Max", "isAllDay": True, "categories": ["URLAUB"],
             "start": {"dateTime": "2026-09-02T00:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-09-03T00:00:00.0000000", "timeZone": "UTC"},
             "organizer": {"emailAddress": {"name": "Max Muster"}}},
        ],
    })
    count = try_auto_ingest_json(
        payload, tool_name="mcp_MSOffice365MCP_m365_get_events", tool_use_id="tc_ev", db_path=db_file,
        tool_args={"calendar": "OFFICEZEITEN", "start_time_iso": "2026-09-01T00:00:00Z", "end_time_iso": "2026-09-14T23:59:59Z"},
    )
    assert int(count) == 2 and count.window == ("2026-09-01", "2026-09-14")
    rows = sqlite3.connect(str(db_file)).execute(
        "SELECT id, reference_key, timestamp, duration_seconds, user_id, category FROM mcp_records ORDER BY id"
    ).fetchall()
    assert rows == [
        ("evt-1", "OFFICEZEITEN", "2026-09-01T08:00:00", 9 * 3600, "Johannes Huchler", "event"),
        ("evt-2", "OFFICEZEITEN", "2026-09-02T00:00:00", 24 * 3600, "Max Muster", "URLAUB"),
    ]


def test_calendar_window_refetch_is_scoped_to_that_calendar(tmp_path: Path):
    db_file = tmp_path / "state.db"

    def _event(id_, day, calendar=None):
        item = {"id": id_, "subject": id_, "start": {"dateTime": f"{day}T08:00:00"}, "end": {"dateTime": f"{day}T09:00:00"}}
        if calendar:
            item["calendar_name"] = calendar
        return item

    args_main = {"calendar": "Kalender", "start_time_iso": "2026-09-01", "end_time_iso": "2026-09-30"}
    args_office = {"calendar": "OFFICEZEITEN", "start_time_iso": "2026-09-01", "end_time_iso": "2026-09-30"}
    tool = "mcp_MSOffice365MCP_m365_get_events"
    try_auto_ingest_json(json.dumps({"value": [_event("m1", "2026-09-01", "Kalender")]}), tool_name=tool,
                         tool_use_id="a", db_path=db_file, tool_args=args_main)
    try_auto_ingest_json(json.dumps({"value": [_event("o1", "2026-09-02", "OFFICEZEITEN")]}), tool_name=tool,
                         tool_use_id="b", db_path=db_file, tool_args=args_office)
    # Re-fetching OFFICEZEITEN replaces only OFFICEZEITEN rows in the window.
    res = try_auto_ingest_json(json.dumps({"value": [_event("o2", "2026-09-03")]}), tool_name=tool,
                               tool_use_id="c", db_path=db_file, tool_args=args_office)
    assert int(res) == 1 and res.replaced == 1
    rows = sqlite3.connect(str(db_file)).execute(
        "SELECT id, reference_key FROM mcp_records ORDER BY id").fetchall()
    assert rows == [("m1", "Kalender"), ("o2", "OFFICEZEITEN")]  # o2 took the calendar from the request


def test_window_pairs_include_calendar_view_arguments():
    from tools.mcp_json_ingestor import _date_window_from_args

    assert _date_window_from_args({"start_time_iso": "2026-08-01T00:00:00Z", "end_time_iso": "2026-09-14T23:59:59Z"}) == ("2026-08-01", "2026-09-14")


# --------------------------------------------------------------------------- AIS-344 month-by-month + fetch register


def test_split_window_args_keeps_value_shapes():
    from tools.mcp_json_ingestor import split_window_args

    # bare dates (Tempo style)
    chunks = split_window_args({"startDate": "2026-01-15", "endDate": "2026-03-10", "user": "me"})
    assert [m for m, _ in chunks] == ["2026-01", "2026-02", "2026-03"]
    assert chunks[0][1] == {"startDate": "2026-01-15", "endDate": "2026-01-31", "user": "me"}
    assert chunks[1][1]["startDate"] == "2026-02-01" and chunks[1][1]["endDate"] == "2026-02-28"
    assert chunks[2][1]["startDate"] == "2026-03-01" and chunks[2][1]["endDate"] == "2026-03-10"
    # datetimes keep the T…Z shape (Graph calendarView style)
    chunks = split_window_args({"start_time_iso": "2026-08-01T00:00:00Z", "end_time_iso": "2026-09-14T23:59:59Z"})
    assert chunks[0][1] == {"start_time_iso": "2026-08-01T00:00:00Z", "end_time_iso": "2026-08-31T23:59:59Z"}
    assert chunks[1][1] == {"start_time_iso": "2026-09-01T00:00:00Z", "end_time_iso": "2026-09-14T23:59:59Z"}
    # one month or no window → run as-is
    assert split_window_args({"startDate": "2026-05-01", "endDate": "2026-05-31"}) == []
    assert split_window_args({"issue": "X"}) == [] and split_window_args(None) == []


def test_completeness_is_read_through_the_envelope():
    from tools.mcp_json_ingestor import _completeness_from_payload

    assert _completeness_from_payload({"result": {"value": [], "months": [{"month": "2026-05", "count": 3, "complete": True}], "complete": True}}) == (True, [{"month": "2026-05", "count": 3, "complete": True}])
    assert _completeness_from_payload({"result": json.dumps({"complete": False})}) == (False, None)
    assert _completeness_from_payload({"value": []}) == (None, None)


def test_fetch_register_and_coverage_per_month(tmp_path: Path):
    from tools.mcp_json_ingestor import fetch_coverage

    db_file = tmp_path / "s.db"
    payload = {"result": {
        "value": [{"id": "e1", "subject": "x", "source_key": "group:g-1", "start": {"dateTime": "2026-05-05T00:00:00"}, "end": {"dateTime": "2026-05-06T00:00:00"}},
                  {"id": "e2", "subject": "y", "source_key": "group:g-1", "start": {"dateTime": "2026-06-02T00:00:00"}, "end": {"dateTime": "2026-06-03T00:00:00"}}],
        "months": [{"month": "2026-05", "count": 1, "complete": True}, {"month": "2026-06", "count": 1, "complete": False, "error": "429 throttled"},
                   {"month": "2026-07", "count": 0, "complete": True}],
        "complete": False,
    }}
    res = try_auto_ingest_json(json.dumps(payload), tool_name="mcp_MSOffice365MCP_m365_get_events", tool_use_id="f1",
                               db_path=db_file, tool_args={"calendar": "OFFICE", "start_time_iso": "2026-05-01", "end_time_iso": "2026-07-31"})
    assert int(res) == 2 and res.complete is False and res.incomplete_months == ["2026-06"]
    conn = sqlite3.connect(str(db_file))
    # reference_key = the stable source key, not the calendar name the model typed
    assert {r[0] for r in conn.execute("SELECT reference_key FROM mcp_records").fetchall()} == {"group:g-1"}
    cov = fetch_coverage(conn, "mcp_MSOffice365MCP_%", "2026-04-01", "2026-08-31", "group:g-1")
    assert [(c["month"], c["status"]) for c in cov] == [
        ("2026-04", "none"), ("2026-05", "complete"), ("2026-06", "incomplete"), ("2026-07", "complete"), ("2026-08", "none")]
    assert cov[2]["error"] == "429 throttled"


def test_incomplete_month_keeps_earlier_rows(tmp_path: Path):
    db_file = tmp_path / "s.db"
    tool = "mcp_MSOffice365MCP_m365_get_events"

    def ev(id_, day):
        return {"id": id_, "subject": id_, "source_key": "group:g-1", "start": {"dateTime": f"{day}T08:00:00"}, "end": {"dateTime": f"{day}T09:00:00"}}

    # complete May fetch (3 rows)
    try_auto_ingest_json(json.dumps({"result": {"value": [ev("a", "2026-05-04"), ev("b", "2026-05-11"), ev("c", "2026-05-18")],
                                                "months": [{"month": "2026-05", "count": 3, "complete": True}], "complete": True}}),
                         tool_name=tool, tool_use_id="f1", db_path=db_file, tool_args={"calendar": "X", "start_time_iso": "2026-05-01", "end_time_iso": "2026-05-31"})
    # a later, truncated May fetch (1 row, complete=false) must not wipe the three
    res = try_auto_ingest_json(json.dumps({"result": {"value": [ev("d", "2026-05-25")],
                                                      "months": [{"month": "2026-05", "count": 1, "complete": False}], "complete": False}}),
                               tool_name=tool, tool_use_id="f2", db_path=db_file, tool_args={"calendar": "X", "start_time_iso": "2026-05-01", "end_time_iso": "2026-05-31"})
    assert res.replaced == 0
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM mcp_records").fetchone()[0] == 4
    # a complete re-fetch replaces the month again
    res = try_auto_ingest_json(json.dumps({"result": {"value": [ev("e", "2026-05-06")],
                                                      "months": [{"month": "2026-05", "count": 1, "complete": True}], "complete": True}}),
                               tool_name=tool, tool_use_id="f3", db_path=db_file, tool_args={"calendar": "X", "start_time_iso": "2026-05-01", "end_time_iso": "2026-05-31"})
    assert res.replaced == 4 and conn.execute("SELECT COUNT(*) FROM mcp_records").fetchone()[0] == 1


def test_empty_complete_window_is_registered(tmp_path: Path):
    from tools.mcp_json_ingestor import fetch_coverage

    db_file = tmp_path / "s.db"
    res = try_auto_ingest_json(json.dumps({"result": {"value": [], "complete": True}}), tool_name="mcp_X_events", tool_use_id="f0",
                               db_path=db_file, tool_args={"start": "2026-02-01", "end": "2026-02-28"})
    assert int(res) == 0 and res.complete is True
    conn = sqlite3.connect(str(db_file))
    assert fetch_coverage(conn, "mcp_X_%", "2026-02-01", "2026-02-28")[0]["status"] == "complete"


def test_ingest_hint_names_incomplete_months():
    from tools.mcp_json_ingestor import IngestResult
    from tools.tool_result_storage import _build_ingest_hint

    res = IngestResult(5, window=("2026-01-01", "2026-03-31"), complete=False,
                       months=[{"month": "2026-01", "count": 3, "complete": True}, {"month": "2026-02", "count": 2, "complete": False, "error": "429"},
                               {"month": "2026-03", "count": 0, "complete": True}])
    hint = _build_ingest_hint("mcp_X_events", res)
    assert "INCOMPLETE for 2026-01-01..2026-03-31" in hint and "2026-02" in hint and "one call per month" in hint
    ok = IngestResult(5, window=("2026-01-01", "2026-01-31"), complete=True, months=[{"month": "2026-01", "count": 5, "complete": True}])
    assert "complete for 2026-01-01..2026-01-31 (2026-01:5)" in _build_ingest_hint("mcp_X_events", ok)


# ---------------------------------------------------------------------------
# AIS-354: NDJSON payloads and blob-row visibility
# ---------------------------------------------------------------------------

def test_ndjson_result_string_yields_one_row_per_event(tmp_path: Path):
    """The split-by-month bridge joined per-month answers with newlines; the
    ingestor must read every document instead of storing one blob row."""
    from tools.mcp_json_ingestor import _extract_items, get_db_connection

    def month_doc(month, n):
        return json.dumps({"result": {"resolved_calendar_name": "Kalender", "value": [
            {"id": f"{month}-{i}", "subject": "Urlaub Johannes Huchler",
             "start_iso_local": f"{month}-0{i + 1}T00:00:00", "end_iso_local": f"{month}-0{i + 2}T00:00:00"}
            for i in range(n)]}})

    joined = "\n".join([month_doc("2026-11", 3), month_doc("2026-12", 2)])
    payload = {"result": joined, "complete": True,
               "months": [{"month": "2026-11", "count": 3, "complete": True}, {"month": "2026-12", "count": 2, "complete": True}]}
    assert len(_extract_items(payload)) == 5

    db_file = tmp_path / "s.db"
    res = try_auto_ingest_json(json.dumps(payload), tool_name="mcp_MSOffice365MCP_m365_get_events", tool_use_id="cal1",
                               db_path=db_file, tool_args={"calendar": "URLAUB | IAMDS", "start_time_iso": "2026-11-01T00:00:00Z", "end_time_iso": "2026-12-31T23:59:59Z"})
    assert int(res) == 5 and res.blob is False
    conn = get_db_connection(db_file)
    assert conn.execute("SELECT COUNT(*) FROM mcp_records WHERE tool_use_id = 'cal1'").fetchone()[0] == 5
    conn.close()


def test_ndjson_requires_every_line_to_parse():
    from tools.mcp_json_ingestor import _parse_ndjson_documents

    assert _parse_ndjson_documents('{"a": 1}\n{"b": 2}') == [{"a": 1}, {"b": 2}]
    assert _parse_ndjson_documents('{"a": 1}') == []           # single document is not NDJSON
    assert _parse_ndjson_documents('{"a": 1}\nnot json') == []
    assert _parse_ndjson_documents("k: v | k2: v2\nk: v | k2: v2") == []


def test_large_unrecognised_document_is_flagged_as_blob(tmp_path: Path):
    db_file = tmp_path / "s.db"
    big = {"result": {"odd": {"shape": True, "filler": "x" * 5000}}}
    res = try_auto_ingest_json(json.dumps(big), tool_name="mcp_X_get_events", tool_use_id="blob1", db_path=db_file)
    assert int(res) == 1 and res.blob is True
    small = {"result": {"odd": {"shape": True}}}
    res = try_auto_ingest_json(json.dumps(small), tool_name="mcp_X_get_events", tool_use_id="blob2", db_path=db_file)
    assert int(res) == 1 and res.blob is False
    # one large but real record (it carries a timestamp) is not a blob
    issue = {"result": {"key": "PROJ-9", "created": "2026-03-01T08:00:00Z", "description": "y" * 5000}}
    res = try_auto_ingest_json(json.dumps(issue), tool_name="mcp_X_get_issue", tool_use_id="blob3", db_path=db_file)
    assert int(res) == 1 and res.blob is False


# AIS-409: a confirmed booking is written through to the listing tool's rows.
def _op_row(eid, spent_on, hours, wp="AIS-408"):
    return {"id": eid, "spent_on": spent_on, "hours": hours, "duration_seconds": int(hours * 3600),
            "work_package_id": wp, "user": "Johannes Huchler", "category": "Development", "comment": "c"}


def _rows(db_file):
    return sqlite3.connect(str(db_file)).execute(
        "SELECT id, tool_name, reference_key, timestamp, duration_seconds FROM mcp_records ORDER BY id"
    ).fetchall()


def test_booking_upserts_into_the_listing_tools_rows(tmp_path: Path):
    db_file = tmp_path / "state.db"
    listing = json.dumps({"window": {"start": "2026-09-01", "end": "2026-09-30"}, "complete": True,
                          "time_entries": [_op_row(10, "2026-09-02", 2.0)]})
    try_auto_ingest_json(listing, tool_name="mcp_op_list_time_entries", tool_use_id="t1", db_path=db_file,
                         tool_args={"date_from": "2026-09-01", "date_to": "2026-09-30"})

    booked = json.dumps({"state": "created", "time_entry": _op_row(11, "2026-09-24", 1.5),
                         "time_entries": [_op_row(11, "2026-09-24", 1.5)]})
    assert int(try_auto_ingest_json(booked, tool_name="mcp_op_log_time", tool_use_id="t2", db_path=db_file)) == 1

    changed = json.dumps({"state": "updated", "time_entries": [_op_row(10, "2026-09-02", 3.0)]})
    try_auto_ingest_json(changed, tool_name="mcp_op_log_time", tool_use_id="t3", db_path=db_file)

    assert _rows(db_file) == [
        ("10", "mcp_op_list_time_entries", "AIS-408", "2026-09-02", 10800),
        ("11", "mcp_op_list_time_entries", "AIS-408", "2026-09-24", 5400),
    ]


def test_deleted_booking_is_removed_from_the_listing_rows(tmp_path: Path):
    db_file = tmp_path / "state.db"
    try_auto_ingest_json(json.dumps({"time_entries": [_op_row(10, "2026-09-02", 2.0), _op_row(11, "2026-09-03", 1.0)]}),
                         tool_name="mcp_op_list_time_entries", tool_use_id="t1", db_path=db_file)
    deleted = json.dumps({"state": "deleted", "deleted_time_entry_id": 11, "time_entry": _op_row(11, "2026-09-03", 1.0)})
    assert int(try_auto_ingest_json(deleted, tool_name="mcp_op_log_time", tool_use_id="t2", db_path=db_file)) == 0
    assert [r[0] for r in _rows(db_file)] == ["10"]


def test_previews_are_never_ingested(tmp_path: Path):
    db_file = tmp_path / "state.db"
    for payload, tool in (
        ({"state": "preview", "ready": True, "would": {"spent_on": "2026-09-24", "hours": 1.5}}, "mcp_op_log_time"),
        ({"state": "duplicate", "work_package": {"id": 1, "subject": "x"}}, "mcp_op_create_work_package"),
        ({"action": "update", "confirmed": False, "requires_confirmation": True, "payload": {"subject": "x"}}, "mcp_op_update_work_package"),
    ):
        assert int(try_auto_ingest_json(json.dumps(payload), tool_name=tool, tool_use_id="p", db_path=db_file)) == 0
    assert not db_file.exists() or _rows(db_file) == []


def test_booking_echo_without_date_or_duration_is_not_a_record(tmp_path: Path):
    db_file = tmp_path / "state.db"
    echo = json.dumps({"result": {"id": 5, "message": "Worklog created"}})
    assert int(try_auto_ingest_json(echo, tool_name="mcp_TempoMCP_createWorklog", tool_use_id="t", db_path=db_file)) == 0


def test_write_through_targets():
    from tools.mcp_json_ingestor import write_through_target

    assert write_through_target("mcp_op_log_time") == "mcp_op_list_time_entries"
    assert write_through_target("mcp_TempoMCP_createWorklog") == "mcp_TempoMCP_retrieveWorklogs"
    assert write_through_target("mcp_AtlassianMCP_jira_add_worklog") == "mcp_AtlassianMCP_jira_get_worklog"
    assert write_through_target("mcp_op_list_time_entries") is None
