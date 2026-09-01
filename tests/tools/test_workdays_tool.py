"""tools/workdays_tool.py — calendar facts as a tool; the profile lives in memory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import workdays_tool as wt
from tools.sql_tool import execute_sql

BY = {"region": "DE-BY", "weekly_hours": 40, "days_per_week": 5}


@pytest.fixture(autouse=True)
def _no_profile(monkeypatch, tmp_path):
    """Default: no memory backend, no legacy config, cache cleared."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    wt._profile_cache.update({"at": 0.0, "profile": None})
    monkeypatch.setattr(wt, "_profile_from_memory", lambda: None)
    monkeypatch.setattr(wt, "_profile_from_legacy_config", lambda: None)


def _run(**args):
    return json.loads(wt.execute_workdays(args))


class TestProfileResolution:
    def test_without_profile_the_tool_asks_instead_of_assuming(self):
        out = _run(action="target_hours", start="2026-01-01", end="2026-01-31")
        assert out["error"] == "worktime profile unknown"
        assert "region" in out["missing"]
        assert "Bayern (DE-BY)" in out["clarify_choices"]
        assert "configure" in out["ask"] and "estimate_profile" in out["estimate"]
        # the error blob stays slim: no 54-entry region list, no defaults block
        assert "valid_regions" not in out and "week_model_defaults" not in out
        assert len(json.dumps(out)) < 700

    def test_parameters_alone_are_enough(self):
        out = _run(action="target_hours", start="2026-01-01", end="2026-01-31", **BY)
        assert out["totals"]["target_hours"] == 160.0
        assert out["assumptions"]["profile_source"]["region"] == "parameter"

    def test_profile_from_memory_fills_the_gaps(self, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: {"region": "DE-BY", "weekly_hours": 40.0, "days_per_week": 5, "half_days": ["12-24", "12-31"], "_source": "memory (mcp)"})
        out = _run(action="target_hours", start="2026-12-01", end="2026-12-31")
        assert out["assumptions"]["region"] == "DE-BY"
        assert out["assumptions"]["profile_source"]["region"] == "memory (mcp)"
        assert out["months"][0]["half_days"] == 2  # 24.12. and 31.12. are Thursdays in 2026
        # explicit parameter still wins
        assert _run(action="workdays", start="2026-01-01", end="2026-01-31", region="AT")["assumptions"]["region"] == "AT"

    def test_legacy_state_key_is_read_but_named_as_such(self, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_legacy_config", lambda: {"region": "DE-BY", "_source": "config (legacy state key)"})
        out = _run(action="holidays", year=2026)
        assert out["region"] == "DE-BY"

    def test_profile_text_roundtrip(self):
        text = wt._profile_text({"region": "CH-ZH", "weekly_hours": 42, "days_per_week": 5, "half_days": ["12-24"], "employment_start": "2026-03-01"})
        parsed = wt._parse_profile_text(text)
        assert parsed == {"region": "CH-ZH", "weekly_hours": 42.0, "days_per_week": 5, "half_days": ["12-24"], "employment_start": "2026-03-01"}

    def test_real_legacy_config_is_parsed(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text("state: BY\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr("hermes_constants.get_config_path", lambda: home / "config.yaml")
        monkeypatch.undo()  # drop the autouse stubs for this one
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr("hermes_constants.get_config_path", lambda: home / "config.yaml")
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: None)
        wt._profile_cache.update({"at": 0.0, "profile": None})
        assert wt._profile_from_legacy_config() == {"region": "DE-BY", "_source": "config (legacy state key)"}


class TestConfigure:
    def test_configure_saves_the_profile_through_the_memory_facade(self, monkeypatch):
        saved = {}

        class _Facade:
            mode = "vault"

            def save(self, **kw):
                saved.update(kw)
                return SimpleNamespace(ok=True, backend="vault", ref="profile/arbeitszeit-profil.md", error=None)

        monkeypatch.setattr(wt, "_facade", lambda: _Facade())
        out = _run(action="configure", region="bayern", weekly_hours=40, days_per_week=5, half_days=["12-24", "12-31"], notes="Firma: 24.12./31.12. halbe Tage")
        assert out["memory"] == {"saved": True, "backend": "vault", "ref": "profile/arbeitszeit-profil.md", "error": None}
        assert saved["title"] == wt.PROFILE_TITLE and saved["type"] == "reference" and "worktime" in saved["tags"]  # not a second `profile` note
        assert "region: DE-BY" in saved["content"] and "half_days: 12-24, 12-31" in saved["content"]
        # the saved profile is used right away, no second lookup
        nxt = _run(action="target_hours", start="2026-01-01", end="2026-01-31")
        assert nxt["totals"]["target_hours"] == 160.0 and nxt["assumptions"]["profile_source"]["region"] == "memory (vault)"

    def test_configure_without_region_is_refused(self):
        out = _run(action="configure", weekly_hours=40)
        assert out.get("success") is False and "ask the user" in out["error"]

    def test_no_memory_backend_keeps_the_profile_for_this_call_only(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        out = _run(action="configure", region="AT-W")
        assert out["memory"]["saved"] is False and out["memory"]["backend"] == "none"
        assert _run(action="holidays", year=2026)["region"] == "AT-W"


class TestActions:
    def test_holidays_marks_weekend_and_partial_days(self):
        out = _run(action="holidays", year=2026, **BY)
        by_date = {h["date"]: h for h in out["holidays"]}
        assert by_date["2026-08-15"]["on_workday"] is False  # Saturday
        assert by_date["2026-08-08"]["kind"] == "partial" and by_date["2026-08-08"]["on_workday"] is False
        assert by_date["2026-04-03"]["name"] == "Karfreitag" and by_date["2026-04-03"]["on_workday"] is True
        assert out["count_on_workdays"] == 9

    def test_target_hours_matches_the_verified_session_table(self):
        out = _run(action="target_hours", start="2026-01-01", end="2026-08-29", **BY)
        months = {m["month"]: m for m in out["months"]}
        assert months["2026-01"]["workdays_net"] == 20 and months["2026-03"]["workdays_net"] == 22
        assert out["totals"]["target_hours"] == 1312.0 and out["totals"]["holidays_on_workdays"] == 8
        assert out["range"]["inclusive"] is True and "target_net" in out["formula"]

    def test_workdays_with_days_and_six_day_week(self):
        out = _run(action="workdays", start="2026-08-01", end="2026-08-31", include_days=True, region="DE-BY", weekly_hours=48, days_per_week=6)
        assert out["totals"]["workdays_net"] == 25 and "target_hours" not in out["totals"]
        sat = next(d for d in out["days"] if d["day"] == "2026-08-15")
        assert sat["reason"] == "holiday" and sat["holiday"] == "Mariä Himmelfahrt"

    def test_days_returns_every_calendar_day_in_one_call(self):
        out = _run(action="days", year=2026, **BY)
        assert len(out["days"]) == 365 and out["totals"]["target_hours"] > 0
        by_day = {d["day"]: d for d in out["days"]}
        assert by_day["2026-04-03"] == {"day": "2026-04-03", "weekday": 5, "factor": 0.0, "reason": "holiday", "holiday": "Karfreitag"}
        assert by_day["2026-12-24"]["factor"] == 0.5 and by_day["2026-08-16"]["reason"] == "weekend"

    def test_invalid_input_is_a_tool_error(self):
        assert _run(action="holidays", year=2026, region="XX")["success"] is False
        assert _run(action="nonsense")["success"] is False
        assert _run(action="workdays", start="2026-02-01", end="2026-01-01", **BY)["success"] is False


class TestMaterialize:
    def test_table_joins_with_ingested_worklogs_without_multiplying(self, tmp_path):
        db = tmp_path / "state.db"
        out = json.loads(wt.execute_workdays(
            {"action": "materialize", "start": "2026-01-01", "end": "2026-01-31",
             "worklog_source_tool": "mcp_TempoMCP_retrieveWorklogs", **BY}, db_path=db))
        assert out["table"] == "workday_calendar" and out["rows"] == 31 and out["totals"]["target_hours"] == 160.0
        assert "mcp_TempoMCP_retrieveWorklogs" in out["example_sql"]  # from the profile/args, not hardcoded

        # two worklogs on one day must not double the target hours
        conn = sqlite3.connect(str(db))
        from tools.mcp_json_ingestor import init_mcp_tables
        init_mcp_tables(conn)
        conn.executemany(
            "INSERT INTO mcp_records (id, tool_name, tool_use_id, reference_key, timestamp, user_id, duration_seconds, category, comment, raw_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [("a", "mcp_TempoMCP_retrieveWorklogs", "t", "EXT-95", "2026-01-08T08:00:00", "", 7200, "", "", "{}"),
             ("b", "mcp_TempoMCP_retrieveWorklogs", "t", "EXT-95", "2026-01-08T10:00:00", "", 3600, "", "", "{}"),
             ("v", "mcp_TempoMCP_retrieveWorklogs", "t", "IAMDS-595", "2026-01-02T08:00:00", "", 3600, "", "", "{}")],
        )
        conn.commit()
        conn.close()
        res = execute_sql(out["example_sql"], db_path=db)
        assert "| 2026-01 | 160.0 | 4.0 |" in res  # 3h work + 1h vacation booking; report excludes vacation, this raw join does not

        # second run replaces the range instead of duplicating it
        json.loads(wt.execute_workdays({"action": "materialize", "start": "2026-01-01", "end": "2026-01-31", **BY}, db_path=db))
        count = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM workday_calendar").fetchone()[0]
        assert count == 31

    def test_materialize_needs_a_profile_too(self, tmp_path):
        out = json.loads(wt.execute_workdays({"action": "materialize", "year": 2026}, db_path=tmp_path / "s.db"))
        assert out["error"] == "worktime profile unknown"

    def test_default_range_is_last_year_to_next_year(self, tmp_path, monkeypatch):
        out = json.loads(wt.execute_workdays({"action": "materialize", **BY}, db_path=tmp_path / "s.db"))
        from datetime import date
        assert out["range"]["start"] == f"{date.today().year - 1}-01-01" and out["range"]["end"] == f"{date.today().year + 1}-12-31"


def test_registered_as_a_core_tool():
    import toolsets
    from tools.registry import registry

    assert "workdays" in toolsets._HERMES_CORE_TOOLS
    assert "workdays" in toolsets.TOOLSETS and toolsets.TOOLSETS["workdays"]["tools"] == ["workdays"]
    assert any(e.name == "workdays" for e in registry._snapshot_entries())


INSERT_MCP = ("INSERT INTO mcp_records (id, tool_name, tool_use_id, reference_key, timestamp, user_id, "
              "duration_seconds, category, comment, raw_data) VALUES (?,?,?,?,?,?,?,?,?,?)")


def _seed_mcp(db, rows):
    conn = sqlite3.connect(str(db))
    from tools.mcp_json_ingestor import init_mcp_tables
    init_mcp_tables(conn)
    conn.executemany(INSERT_MCP, rows)
    conn.commit()
    conn.close()


class TestNewProfileKeys:
    def test_profile_text_roundtrip_with_all_new_keys(self):
        profile = {
            "region": "DE-BY", "weekly_hours": 20.0, "days_per_week": 3,
            "work_weekdays": ["mo", "tu", "we"], "employment_label": "teilzeit",
            "half_days": ["12-24"], "worklog_source_tool": "mcp_TempoMCP_retrieveWorklogs",
            "vacation_booking_patterns": "IAMDS-595, IAMDS-9%", "vacation_hour_factor": 8.0,
        }
        parsed = wt._parse_profile_text(wt._profile_text(profile))
        assert parsed["work_weekdays"] == ["mo", "tu", "we"]
        assert parsed["employment_label"] == "teilzeit"
        assert parsed["worklog_source_tool"] == "mcp_TempoMCP_retrieveWorklogs"
        assert parsed["vacation_booking_patterns"] == "IAMDS-595, IAMDS-9%"
        assert parsed["vacation_hour_factor"] == 8.0

    def test_configure_validates_the_new_keys(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        out = _run(action="configure", region="DE-BY", work_weekdays=["mo", "di", "mi"], days_per_week=5)
        assert out["success"] is False and "contradicts" in out["error"]
        assert _run(action="configure", region="DE-BY", employment_label="fulltime")["success"] is False
        assert _run(action="configure", region="DE-BY", vacation_hour_factor=-1)["success"] is False
        ok = _run(action="configure", region="DE-BY", weekly_hours=20, work_weekdays=["mo", "di", "mi"],
                  employment_label="teilzeit", worklog_source_tool="my_worklog_tool",
                  vacation_booking_patterns="VAC-1", vacation_hour_factor=8)
        assert ok["profile"]["days_per_week"] == 3 and ok["profile"]["work_weekdays"] == ["mo", "tu", "we"]

    def test_target_hours_with_explicit_weekdays(self):
        out = _run(action="target_hours", start="2026-04-01", end="2026-04-12", region="DE-BY",
                   weekly_hours=20, work_weekdays=["mo", "di", "mi"])
        assert out["assumptions"]["work_weekdays"] == ["Mo", "Tu", "We"]
        assert out["assumptions"]["weekend"] == "Th+Fr+Sa+Su"
        assert out["totals"]["target_hours"] == 20.0  # 3 working days x 6.6667 h


class TestAbsences:
    def test_add_range_expands_to_working_days_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(BY, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        db = tmp_path / "s.db"
        out = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "add", "days": [{"from": "2026-08-03", "to": "2026-08-14"}]}, db_path=db))
        assert out["upserted"] == 10  # two full Mo-Fr weeks, weekends skipped
        again = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "add", "days": [{"from": "2026-08-03", "to": "2026-08-14"}]}, db_path=db))
        assert again["summary"][0]["days"] == 10  # UPSERT, not duplicated

    def test_import_from_bookings_converts_hours_to_day_portions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, vacation_booking_patterns="IAMDS-595", vacation_hour_factor=8.0, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        db = tmp_path / "s.db"
        _seed_mcp(db, [
            ("v1", "t", "u", "IAMDS-595", "2026-08-03T08:00:00", "", 3600, "", "", "{}"),   # 1h x 8 / 8h = full day
            ("v2", "t", "u", "IAMDS-595", "2026-08-04T08:00:00", "", 1800, "", "", "{}")])  # 0.5h -> half day
        out = json.loads(wt.execute_workdays({"action": "absences", "op": "import_from_bookings"}, db_path=db))
        assert out["upserted"] == 2 and out["vacation_hour_factor"] == 8.0
        rows = dict(sqlite3.connect(str(db)).execute("SELECT day, portion FROM absences").fetchall())
        assert rows["2026-08-03"] == 1.0 and rows["2026-08-04"] == 0.5

    def test_import_without_patterns_asks_for_a_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(BY, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "import_from_bookings"}, db_path=tmp_path / "s.db"))
        assert out["error"] == "no vacation_booking_patterns configured" and "vault" in out["ask"]

    def test_remove_refuses_to_wipe_without_filter(self, tmp_path):
        out = json.loads(wt.execute_workdays({"action": "absences", "op": "remove"}, db_path=tmp_path / "s.db"))
        assert out["success"] is False


class TestEstimateProfile:
    def test_estimate_proposes_week_model_and_demands_confirmation(self, tmp_path):
        from datetime import date as _date, timedelta as _td
        base = _date.today() - _td(weeks=10)
        base -= _td(days=base.weekday())  # a Monday, safely in the past
        rows, i = [], 0
        for week in range(8):
            for offset in (0, 1, 2):  # Mon, Tue, Wed
                d = base + _td(days=week * 7 + offset)
                rows.append((f"r{i}", "mcp_MyTimeMCP_getWorklogs", "u", f"PROJ-{i}",
                             f"{d.isoformat()}T08:00:00", "", int(6.67 * 3600), "", "", "{}"))
                i += 1
        db = tmp_path / "s.db"
        _seed_mcp(db, rows)
        out = json.loads(wt.execute_workdays({"action": "estimate_profile"}, db_path=db))
        assert out["proposal"]["worklog_source_tool"] == "mcp_MyTimeMCP_getWorklogs"
        assert out["proposal"]["work_weekdays"] == ["mo", "tu", "we"]
        assert out["proposal"]["weekly_hours"] == 20.0
        assert "region" in out["missing"] and "CONFIRM" in out["next"]

    def test_estimate_with_empty_db_asks_directly(self, tmp_path):
        out = json.loads(wt.execute_workdays({"action": "estimate_profile"}, db_path=tmp_path / "s.db"))
        assert out["error"].startswith("no ingested worklog data")
        assert "clarify_choices" in out


class TestReport:
    def _seed(self, db, monkeypatch, **extra):
        profile = dict(BY, worklog_source_tool="mcp_MyTimeMCP_%", vacation_booking_patterns="VAC-1",
                       vacation_hour_factor=8.0, _source="memory (mcp)")
        profile.update(extra)
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: profile)
        wt._profile_cache.update({"at": 0.0, "profile": None})
        _seed_mcp(db, [
            ("w1", "mcp_MyTimeMCP_getWorklogs", "u", "PROJ-1", "2026-01-05T08:00:00", "", 8 * 3600, "", "", "{}"),
            ("w2", "mcp_MyTimeMCP_getWorklogs", "u", "PROJ-1", "2026-01-06T09:00:00", "", 4 * 3600, "", "", "{}"),
            ("vc", "mcp_MyTimeMCP_getWorklogs", "u", "VAC-1", "2026-01-07T08:00:00", "", 3600, "", "", "{}"),
        ])

    def test_report_computes_target_actual_vacation_delta_in_sql(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        json.loads(wt.execute_workdays({"action": "absences", "op": "import_from_bookings"}, db_path=db))
        out = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-01-01", "end": "2026-01-31"}, db_path=db))
        assert out["totals"] == {"target_gross": 160.0, "vacation_credit": 8.0, "target_net": 152.0,
                                 "actual": 12.0, "delta": -140.0}
        assert out["months"][0]["month"] == "2026-01"
        assert out["coverage"]["worklog_sources"][0]["rows"] == 3
        assert "clamped_to_today" not in out

    def test_report_clamps_future_ranges_and_adds_full_target(self, tmp_path, monkeypatch):
        from datetime import date as _date, timedelta as _td
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        future = (_date.today() + _td(days=30)).isoformat()
        out = json.loads(wt.execute_workdays({"action": "report", "start": "2026-01-01", "end": future}, db_path=db))
        assert out["clamped_to_today"] is True and out["requested_range"]["end"] == future
        assert out["range"]["end"] == _date.today().isoformat()
        assert out["target_full_range"] >= out["totals"]["target_gross"]

    def test_report_without_source_pattern_is_a_compact_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(BY, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-01-01", "end": "2026-01-31"}, db_path=tmp_path / "s.db"))
        assert out["missing"] == ["worklog_source_tool"] and "estimate" in out

    def test_report_with_no_matching_rows_names_the_pattern(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch, worklog_source_tool="mcp_OtherTool_%")
        out = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-01-01", "end": "2026-01-31"}, db_path=db))
        assert out["totals"]["actual"] == 0.0
        assert any("mcp_OtherTool_%" in h for h in out["hints"])
        assert any("no absences" in h for h in out["hints"])


class TestAbsencesRefresh:
    def test_reimport_drops_cancelled_booking_rows_in_window(self, tmp_path, monkeypatch):
        """A cancelled/moved booking must disappear from absences on re-import
        (AIS-275): derived 'bookings:%' rows in the window are replaced."""
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, vacation_booking_patterns="IAMDS-595", vacation_hour_factor=8.0, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        db = tmp_path / "s.db"
        _seed_mcp(db, [
            ("v1", "t", "u", "IAMDS-595", "2026-09-07T08:00:00", "", 3600, "", "", "{}"),
            ("v2", "t", "u", "IAMDS-595", "2026-09-08T08:00:00", "", 3600, "", "", "{}"),
        ])
        out = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "import_from_bookings", "start": "2026-09-01", "end": "2026-09-30"},
            db_path=db))
        assert out["upserted"] == 2

        # Upstream: the vacation moved — v1/v2 deleted, new booking v3.
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM mcp_records WHERE id IN ('v1', 'v2')")
        conn.execute(INSERT_MCP, ("v3", "t", "u", "IAMDS-595", "2026-09-14T08:00:00", "", 3600, "", "", "{}"))
        conn.commit(); conn.close()

        out = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "import_from_bookings", "start": "2026-09-01", "end": "2026-09-30"},
            db_path=db))
        assert out["deleted"] == 2 and out["upserted"] == 1
        days = sorted(r[0] for r in sqlite3.connect(str(db)).execute(
            "SELECT day FROM absences WHERE kind = 'vacation'").fetchall())
        assert days == ["2026-09-14"]

    def test_reimport_keeps_user_added_days(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, vacation_booking_patterns="IAMDS-595", vacation_hour_factor=8.0, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        db = tmp_path / "s.db"
        json.loads(wt.execute_workdays(
            {"action": "absences", "op": "add", "days": ["2026-09-21"], "source": "user"}, db_path=db))
        out = json.loads(wt.execute_workdays(
            {"action": "absences", "op": "import_from_bookings", "start": "2026-09-01", "end": "2026-09-30"},
            db_path=db))
        assert out["deleted"] == 0  # only bookings:% rows are replaced
        days = [r[0] for r in sqlite3.connect(str(db)).execute("SELECT day FROM absences").fetchall()]
        assert days == ["2026-09-21"]


class TestReportDataAge:
    def test_report_coverage_carries_fetch_age(self, tmp_path, monkeypatch):
        profile = dict(BY, worklog_source_tool="mcp_MyTimeMCP_%", _source="memory (mcp)")
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: profile)
        wt._profile_cache.update({"at": 0.0, "profile": None})
        db = tmp_path / "s.db"
        _seed_mcp(db, [
            ("w1", "mcp_MyTimeMCP_getWorklogs", "u", "PROJ-1", "2026-01-05T08:00:00", "", 8 * 3600, "", "", "{}"),
        ])
        out = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-01-01", "end": "2026-01-31"}, db_path=db))
        src = out["coverage"]["worklog_sources"][0]
        assert src["last_fetched_at"]  # created_at of the mirror row (UTC)


class TestPartialHolidays:
    """AIS-277: municipal/partial holidays via ask-and-store profile keys."""

    def test_profile_text_roundtrip_including_empty_sentinel(self):
        parsed = wt._parse_profile_text(wt._profile_text({
            "region": "DE-BY", "weekly_hours": 40.0, "days_per_week": 5,
            "partial_holidays": ["Augsburger Friedensfest"],
            "municipality": "Augsburg", "plz": "86159",
        }))
        assert parsed["partial_holidays"] == ["Augsburger Friedensfest"]
        assert parsed["municipality"] == "Augsburg" and parsed["plz"] == "86159"
        # user confirmed "none apply" survives as an EMPTY list, not as unset
        parsed_none = wt._parse_profile_text(wt._profile_text({
            "region": "DE-BY", "weekly_hours": 40.0, "days_per_week": 5, "partial_holidays": [],
        }))
        assert parsed_none["partial_holidays"] == []

    def test_unresolved_hint_appears_and_clears(self, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(BY, _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = _run(action="target_hours", start="2025-08-01", end="2025-08-31")
        assert out["partial_holidays_unresolved"]["names"] == ["Augsburger Friedensfest"]
        assert "configure" in out["partial_holidays_unresolved"]["ask"]
        assert out["totals"]["target_hours"] == 160.0  # 20 workdays (Mariä Himmelfahrt already statutory) — partial NOT deducted while unresolved

        # user confirmed none apply → hint gone, still no deduction
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, partial_holidays=[], _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = _run(action="target_hours", start="2025-08-01", end="2025-08-31")
        assert "partial_holidays_unresolved" not in out
        assert out["assumptions"]["partial_holidays"] == "none apply (user confirmed)"
        assert out["totals"]["target_hours"] == 160.0

    def test_applicable_partial_reduces_target_and_marks_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, partial_holidays=["Augsburger Friedensfest"], _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = _run(action="target_hours", start="2025-08-01", end="2025-08-31")
        assert out["totals"]["target_hours"] == 152.0  # one 8h day less than the 160h statutory baseline
        assert "partial_holidays_unresolved" not in out
        assert out["assumptions"]["partial_holidays"] == ["Augsburger Friedensfest"]

        mat = json.loads(wt.execute_workdays(
            {"action": "materialize", "start": "2025-08-01", "end": "2025-08-31"}, db_path=tmp_path / "s.db"))
        assert mat["totals"]["target_hours"] == 152.0
        row = sqlite3.connect(str(tmp_path / "s.db")).execute(
            "SELECT holiday_name, holiday_kind, factor FROM workday_calendar WHERE day = '2025-08-08'"
        ).fetchone()
        assert row == ("Augsburger Friedensfest", "partial", 0.0)

    def test_holidays_action_marks_applicable_partial_as_workday(self, monkeypatch):
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(
            BY, partial_holidays=["Augsburger Friedensfest"], _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = _run(action="holidays", year=2025)
        friedensfest = next(h for h in out["holidays"] if h["date"] == "2025-08-08")
        assert friedensfest["kind"] == "partial" and friedensfest["on_workday"] is True

    def test_configure_validates_and_suggests(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        bad = _run(action="configure", region="DE-BY", partial_holidays=["Oktoberfest"])
        assert bad["success"] is False and "Augsburger Friedensfest" in bad["error"]

        # plz evidence without a decision → suggestion + confirm, never auto-saved
        out = _run(action="configure", region="DE-BY", weekly_hours=40, days_per_week=5, plz="86159")
        assert out["partial_holiday_suggestions"] == {"Augsburger Friedensfest": True}
        assert "confirm" in out and "partial_holidays" not in out["profile"]

        # explicit decision (canonicalized case) → stored, no suggestions
        out = _run(action="configure", region="DE-BY", partial_holidays=["augsburger friedensfest"])
        assert out["profile"]["partial_holidays"] == ["Augsburger Friedensfest"]
        assert "partial_holiday_suggestions" not in out

        bad_plz = _run(action="configure", region="DE-BY", plz="ABC")
        assert bad_plz["success"] is False

    def test_configure_merges_with_stored_profile(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        _run(action="configure", region="DE-BY", weekly_hours=20, work_weekdays=["mo", "di", "mi"],
             worklog_source_tool="my_tool_%")
        # follow-up with ONLY partial_holidays must not wipe the week model
        out = _run(action="configure", partial_holidays=["Augsburger Friedensfest"])
        assert out["profile"]["region"] == "DE-BY"
        assert out["profile"]["weekly_hours"] == 20.0
        assert out["profile"]["work_weekdays"] == ["mo", "tu", "we"]
        assert out["profile"]["worklog_source_tool"] == "my_tool_%"
        assert out["profile"]["partial_holidays"] == ["Augsburger Friedensfest"]
