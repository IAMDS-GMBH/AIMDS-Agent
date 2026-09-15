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
        assert out["memory"] == {"saved": True, "backend": "vault", "ref": "profile/arbeitszeit-profil.md", "error": None, "mirror": "state.db"}
        assert saved["title"] == wt.PROFILE_TITLE and saved["type"] == "reference" and "worktime" in saved["tags"]  # not a second `profile` note
        assert "region: DE-BY" in saved["content"] and "half_days: 12-24, 12-31" in saved["content"]
        # the saved profile is used right away, no second lookup
        nxt = _run(action="target_hours", start="2026-01-01", end="2026-01-31")
        assert nxt["totals"]["target_hours"] == 160.0 and nxt["assumptions"]["profile_source"]["region"] == "memory (vault)"

    def test_configure_without_region_is_refused(self):
        out = _run(action="configure", weekly_hours=40)
        assert out.get("success") is False and "ask the user" in out["error"]

    def test_no_memory_backend_keeps_the_profile_in_the_local_mirror(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        out = _run(action="configure", region="AT-W")
        # AIS-337: without a memory backend the profile still survives in state.db
        assert out["memory"]["saved"] is True and out["memory"]["backend"] == "state.db"
        assert out["memory"]["mirror"] == "state.db"
        assert _run(action="holidays", year=2026)["region"] == "AT-W"
        wt._profile_cache.update({"at": 0.0, "profile": None})
        prof = _run(action="profile")
        assert prof["profile"]["region"] == "AT-W" and prof["source"] == "state.db mirror"

    def test_mirror_write_failure_falls_back_to_call_only(self, monkeypatch):
        monkeypatch.setattr(wt, "_facade", lambda: SimpleNamespace(mode="none"))
        monkeypatch.setattr(wt, "_save_profile_mirror", lambda profile, db_path=None: False)
        out = _run(action="configure", region="AT-W")
        assert out["memory"]["saved"] is False and out["memory"]["backend"] == "none"


class TestProfileRoundTrip:
    """AIS-337 (session 20260915_082908): configure said `saved: true` since
    2026-08-31, every later session got `worktime profile unknown`. The
    memory MCP's search returns a truncated `snippet` (no `content`), and
    `read(slug)` returns the whole memory object as a JSON string."""

    class _Facade:
        mode = "mcp"

        def __init__(self, title="Arbeitszeit-Profil", slug="arbeitszeit-profil", read_json=True):
            self.title, self.slug, self.read_json = title, slug, read_json
            self.text = wt._profile_text({"region": "DE-BY", "weekly_hours": 40, "days_per_week": 5,
                                          "worklog_source_tool": "mcp_TempoMCP_%"})
            self.reads = []

        def search(self, query, *, limit=5):
            noise = {"title": "Hermes workdays tool: DACH holidays", "slug": "hermes-workdays-tool", "snippet": "Built 2026-08-29 …"}
            hit = {"title": self.title, "slug": self.slug, "snippet": self.text[:40] + "…", "type": "reference"}
            return [noise, hit]

        def read(self, slug):
            self.reads.append(slug)
            if self.read_json:
                return json.dumps({"slug": slug, "title": self.title, "content": self.text, "priority": 8})
            return self.text

    def _fresh(self, monkeypatch, facade):
        monkeypatch.undo()
        monkeypatch.setattr(wt, "_facade", lambda: facade)
        monkeypatch.setattr(wt, "_profile_from_legacy_config", lambda: None)
        wt._profile_cache.update({"at": 0.0, "profile": None})

    def test_snippet_hit_is_completed_by_reading_the_json_payload(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        facade = self._Facade()
        self._fresh(monkeypatch, facade)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        profile = wt._profile_from_memory()
        assert profile["region"] == "DE-BY" and profile["weekly_hours"] == 40.0
        assert profile["worklog_source_tool"] == "mcp_TempoMCP_%" and profile["_source"] == "memory (mcp)"
        assert facade.reads == ["arbeitszeit-profil"]

    def test_updated_title_suffix_and_plain_text_read_still_match(self, monkeypatch, tmp_path):
        facade = self._Facade(title="Arbeitszeit-Profil (updated)", slug="arbeitszeit-profil-updated", read_json=False)
        self._fresh(monkeypatch, facade)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        assert wt._profile_from_memory()["region"] == "DE-BY"

    def test_unrelated_hits_are_ignored(self, monkeypatch, tmp_path):
        facade = self._Facade(title="Arbeitszeit-Notizen", slug="arbeitszeit-notizen")
        self._fresh(monkeypatch, facade)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        assert wt._profile_from_memory() is None

    def test_load_profile_prefers_memory_then_mirror_then_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        assert wt.load_profile(force=True) is None
        assert wt._save_profile_mirror({"region": "CH-ZH", "weekly_hours": 42, "days_per_week": 5})
        assert wt.load_profile(force=True)["_source"] == "state.db mirror"
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: {"region": "AT", "weekly_hours": 38.5, "days_per_week": 5, "_source": "memory (mcp)"})
        assert wt.load_profile(force=True)["region"] == "AT"


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
        assert {k: out["totals"][k] for k in ("target_gross", "vacation_credit", "target_net", "actual", "delta")} == {
            "target_gross": 160.0, "vacation_credit": 8.0, "target_net": 152.0, "actual": 12.0, "delta": -140.0}
        # 2026-01-06 is Heilige Drei Könige in BY: booked, but no target → not a home-office day
        assert out["totals"]["homeoffice_days"] == 1 and out["totals"]["office_days"] == 0 and out["totals"]["absence_days"] == 1
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


class TestPeriods:
    def test_relative_periods_resolve_from_today(self, monkeypatch):
        from datetime import date as _date
        monkeypatch.setattr(wt, "_today", lambda: _date(2026, 9, 15))  # Tuesday, KW38
        pr = wt._period_range
        assert pr("through_last_week", _date(2026, 9, 15)) == (_date(2026, 1, 1), _date(2026, 9, 13))
        assert pr("last_week", _date(2026, 9, 15)) == (_date(2026, 9, 7), _date(2026, 9, 13))
        assert pr("this_week", _date(2026, 9, 15)) == (_date(2026, 9, 14), _date(2026, 9, 20))
        assert pr("mtd", _date(2026, 9, 15)) == (_date(2026, 9, 1), _date(2026, 9, 15))
        assert pr("last_month", _date(2026, 9, 15)) == (_date(2026, 8, 1), _date(2026, 8, 31))
        assert pr("this_month", _date(2026, 9, 15)) == (_date(2026, 9, 1), _date(2026, 9, 30))
        assert pr("ytd", _date(2026, 9, 15), _date(2026, 3, 1)) == (_date(2026, 3, 1), _date(2026, 9, 15))
        with pytest.raises(ValueError):
            pr("gestern", _date(2026, 9, 15))
        out = _run(action="target_hours", period="last_week", **BY)
        assert out["range"] == {"start": "2026-09-07", "end": "2026-09-13", "inclusive": True}


class TestReportDays:
    def _seed(self, db, monkeypatch, today=None):
        from datetime import date as _date
        profile = dict(BY, worklog_source_tool="mcp_MyTimeMCP_%", vacation_booking_patterns="VAC-1",
                       vacation_hour_factor=8.0, _source="memory (mcp)")
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: profile)
        monkeypatch.setattr(wt, "_today", lambda: today or _date(2026, 9, 15))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        _seed_mcp(db, [
            # Tue 2026-09-01: 08:00 + 4h, 12:30 + 4.75h → 08:00 … 17:15, 8.75 h
            ("w1", "mcp_MyTimeMCP_getWorklogs", "u", "EXT-95", "2026-09-01T08:00:00", "", 4 * 3600, "", "", "{}"),
            ("w2", "mcp_MyTimeMCP_getWorklogs", "u", "EXT-95", "2026-09-01T12:30:00", "", int(4.75 * 3600), "", "", "{}"),
            # Wed 2026-09-02: short day
            ("w3", "mcp_MyTimeMCP_getWorklogs", "u", "AIS-1", "2026-09-02T09:00:00", "", 4 * 3600, "", "", "{}"),
            # Sat 2026-09-12: booked on a weekend
            ("w4", "mcp_MyTimeMCP_getWorklogs", "u", "AIS-1", "2026-09-12T10:00:00", "", 3600, "", "", "{}"),
            # vacation booking Thu 2026-09-03 (1h = 8h)
            ("vc", "mcp_MyTimeMCP_getWorklogs", "u", "VAC-1", "2026-09-03T08:00:00", "", 3600, "", "", "{}"),
        ])

    def test_day_rows_carry_start_end_hours_status_and_presence(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        json.loads(wt.execute_workdays({"action": "absences", "op": "import_from_bookings"}, db_path=db))
        json.loads(wt.execute_workdays({"action": "presence", "op": "add", "days": ["2026-09-01"], "source": "user"}, db_path=db))
        out = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-09-01", "end": "2026-09-13", "include_days": True}, db_path=db))
        by_day = {d["day"]: d for d in out["days"]}
        assert len(out["days"]) == 13
        d1 = by_day["2026-09-01"]
        assert (d1["weekday"], d1["first_start"], d1["last_end"], d1["actual"]) == ("Tu", "08:00", "17:15", 8.75)
        assert d1["status"] == "over" and d1["presence"] == "office" and d1["references"] == "EXT-95"
        d2 = by_day["2026-09-02"]
        assert d2["status"] == "short" and d2["presence"] == "homeoffice" and d2["last_end"] == "13:00"
        d3 = by_day["2026-09-03"]
        assert d3["status"] == "absent" and d3["presence"] == "vacation" and d3["absence"]["portion"] == 1.0
        d4 = by_day["2026-09-04"]
        assert d4["status"] == "no_booking" and d4["presence"] == "none"
        sat = by_day["2026-09-12"]
        assert sat["weekend"] is True and sat["status"] == "off_booked" and sat["weekday"] == "Sa"
        assert by_day["2026-09-13"]["status"] == "off"
        m = out["months"][0]
        assert (m["office_days"], m["homeoffice_days"], m["absence_days"], m["travel_days"]) == (1, 1, 1, 0)
        assert out["totals"]["office_days"] == 1 and out["totals"]["homeoffice_days"] == 1
        assert "days" not in json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-09-01", "end": "2026-09-13"}, db_path=db))

    def test_period_report_resolves_the_range_and_names_it(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        out = json.loads(wt.execute_workdays({"action": "report", "period": "through_last_week"}, db_path=db))
        assert out["range"] == {"start": "2026-01-01", "end": "2026-09-13", "inclusive": True, "resolved_from": "through_last_week"}
        assert out["requested_range"]["period"] == "through_last_week"

    def test_write_vault_overwrites_one_canonical_file(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        vault = tmp_path / "vault"
        (vault / "reports").mkdir(parents=True)
        monkeypatch.setattr("agent.memory_facade.workspace_root", lambda: vault)
        monkeypatch.setenv("HERMES_LANGUAGE", "de")
        json.loads(wt.execute_workdays({"action": "absences", "op": "import_from_bookings"}, db_path=db))
        out = json.loads(wt.execute_workdays(
            {"action": "report", "period": "mtd", "include_days": True, "write": "vault"}, db_path=db))
        rf = out["report_file"]
        assert rf["written"] is True and rf["overwritten"] is False and rf["language"] == "de"
        path = Path(rf["path"])
        assert path == vault / "reports" / "worklog" / "arbeitszeit-mtd.md"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\ntype: report\n") and "created: 2026-09-15" in text and "updated: 2026-09-15" in text
        assert "## Ergebnis" in text and "## Tage" in text and "| 2026-09-01 | Di | 08:00 | 17:15 | 8.75 |" in text
        assert "workdays(action='report', period='mtd', include_days=True, write='vault')" in text
        assert "-final" not in path.name and "-korrigiert" not in path.name
        # rerun: same file, created kept, updated bumped
        path.write_text(text.replace("created: 2026-09-15", "created: 2026-09-01"), encoding="utf-8")
        monkeypatch.setattr(wt, "_today", lambda: __import__("datetime").date(2026, 9, 16))
        out2 = json.loads(wt.execute_workdays(
            {"action": "report", "period": "mtd", "include_days": True, "write": "vault"}, db_path=db))
        assert Path(out2["report_file"]["path"]) == path and out2["report_file"]["overwritten"] is True
        text2 = path.read_text(encoding="utf-8")
        assert "created: 2026-09-01" in text2 and "updated: 2026-09-16" in text2
        assert sorted(p.name for p in (vault / "reports" / "worklog").iterdir()) == ["arbeitszeit-mtd.md"]

    def test_write_without_vault_reports_the_gap(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed(db, monkeypatch)
        monkeypatch.setattr("agent.memory_facade.workspace_root", lambda: None)
        out = json.loads(wt.execute_workdays({"action": "report", "period": "mtd", "write": "vault"}, db_path=db))
        assert out["report_file"]["written"] is False and "no vault" in out["report_file"]["error"]
        assert json.loads(wt.execute_workdays({"action": "report", "period": "mtd", "write": "pdf"}, db_path=db))["success"] is False

    def test_report_period_label(self):
        from datetime import date as _date
        assert wt._report_period_label("through_last_week", _date(2026, 1, 1), _date(2026, 9, 13)) == "through-last-week"
        assert wt._report_period_label(None, _date(2026, 1, 1), _date(2026, 12, 31)) == "2026"
        assert wt._report_period_label(None, _date(2026, 9, 1), _date(2026, 9, 30)) == "2026-09"
        assert wt._report_period_label(None, _date(2026, 8, 1), _date(2026, 9, 14)) == "2026-08-01_2026-09-14"


class TestPresence:
    def _seed_events(self, db, monkeypatch):
        profile = dict(BY, worklog_source_tool="mcp_MyTimeMCP_%", _source="memory (mcp)")
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: profile)
        wt._profile_cache.update({"at": 0.0, "profile": None})
        _seed_mcp(db, [
            ("e1", "mcp_MSOffice365MCP_m365_get_events", "c", "OFFICEZEITEN", "2026-09-01T08:00:00", "Johannes Huchler",
             9 * 3600, "event", "Johannes Huchler Büro", '{"subject": "Johannes Huchler", "calendar_name": "OFFICEZEITEN"}'),
            ("e2", "mcp_MSOffice365MCP_m365_get_events", "c", "OFFICEZEITEN", "2026-09-02T08:00:00", "Max Muster",
             9 * 3600, "event", "Max", '{"subject": "Max Muster", "calendar_name": "OFFICEZEITEN"}'),
            ("e3", "mcp_MSOffice365MCP_m365_get_events", "c", "Kalender", "2026-09-03T08:00:00", "Johannes Huchler",
             3600, "event", "Daily", '{"subject": "IAMDS Daily", "organizer": {"emailAddress": {"name": "Johannes Huchler"}}}'),
            ("w1", "mcp_MyTimeMCP_getWorklogs", "u", "AIS-1", "2026-09-02T09:00:00", "", 8 * 3600, "", "", "{}"),
        ])

    def test_import_from_calendar_marks_only_matching_days_of_that_calendar(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed_events(db, monkeypatch)
        out = json.loads(wt.execute_workdays({
            "action": "presence", "op": "import_from_calendar", "calendar": "officezeiten",
            "match": "Johannes Huchler, Johannes", "start": "2026-09-01", "end": "2026-09-30"}, db_path=db))
        assert out["upserted"] == 1 and out["coverage"]["events"] == 2 and out["coverage"]["first_day"] == "2026-09-01"
        assert out["calendar"] == "OFFICEZEITEN"  # canonical casing from the ingested rows
        assert out["summary"] == [{"month": "2026-09", "kind": "office", "days": 1, "sources": "calendar:OFFICEZEITEN"}]
        rep = json.loads(wt.execute_workdays(
            {"action": "report", "start": "2026-09-01", "end": "2026-09-04", "include_days": True}, db_path=db))
        by_day = {d["day"]: d for d in rep["days"]}
        assert by_day["2026-09-01"]["presence"] == "office" and by_day["2026-09-02"]["presence"] == "homeoffice"
        assert rep["totals"]["office_days"] == 1 and rep["totals"]["homeoffice_days"] == 1
        assert rep["coverage"]["presence_sources"][0]["source"] == "calendar:OFFICEZEITEN"
        # reimport replaces the calendar-derived rows, never user-added ones
        json.loads(wt.execute_workdays({"action": "presence", "op": "add", "days": ["2026-09-04"], "kind": "travel"}, db_path=db))
        again = json.loads(wt.execute_workdays({
            "action": "presence", "op": "import_from_calendar", "calendar": "OFFICEZEITEN",
            "match": "Johannes", "start": "2026-09-01", "end": "2026-09-30"}, db_path=db))
        assert again["deleted"] == 1 and again["upserted"] == 1
        assert {(r["kind"], r["days"]) for r in again["summary"]} == {("office", 1), ("travel", 1)}

    def test_import_without_events_or_config_explains_what_to_do(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed_events(db, monkeypatch)
        out = json.loads(wt.execute_workdays({"action": "presence", "op": "import_from_calendar"}, db_path=db))
        assert out["missing"] == ["calendar", "match"] and "configure" in out["ask"]
        out = json.loads(wt.execute_workdays({
            "action": "presence", "op": "import_from_calendar", "calendar": "URLAUB", "match": "Johannes"}, db_path=db))
        assert out["upserted"] == 0 and "m365_get_events(calendar='URLAUB'" in out["hints"][0]
        out = json.loads(wt.execute_workdays({
            "action": "presence", "op": "import_from_calendar", "calendar": "OFFICEZEITEN", "match": "Nobody"}, db_path=db))
        assert out["upserted"] == 0 and "none match" in out["hints"][0]

    def test_presence_profile_keys_roundtrip_and_default_the_import(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        self._seed_events(db, monkeypatch)
        text = wt._profile_text({"region": "DE-BY", "weekly_hours": 40, "days_per_week": 5,
                                 "presence_calendar": "OFFICEZEITEN", "presence_match_patterns": "Johannes Huchler, Johannes"})
        parsed = wt._parse_profile_text(text)
        assert parsed["presence_calendar"] == "OFFICEZEITEN" and parsed["presence_match_patterns"] == "Johannes Huchler, Johannes"
        monkeypatch.setattr(wt, "_profile_from_memory", lambda: dict(parsed, worklog_source_tool="mcp_MyTimeMCP_%", _source="memory (mcp)"))
        wt._profile_cache.update({"at": 0.0, "profile": None})
        out = json.loads(wt.execute_workdays({"action": "presence", "op": "import_from_calendar"}, db_path=db))
        assert out["calendar"] == "OFFICEZEITEN" and out["upserted"] == 1

    def test_remove_refuses_to_wipe_and_add_validates_kind(self, tmp_path):
        db = tmp_path / "s.db"
        assert json.loads(wt.execute_workdays({"action": "presence", "op": "remove"}, db_path=db))["success"] is False
        assert json.loads(wt.execute_workdays({"action": "presence", "op": "add", "days": ["2026-09-01"], "kind": "beach"}, db_path=db))["success"] is False
        out = json.loads(wt.execute_workdays({"action": "presence", "op": "add", "days": [{"from": "2026-09-01", "to": "2026-09-03"}]}, db_path=db))
        assert out["upserted"] == 3
        out = json.loads(wt.execute_workdays({"action": "presence", "op": "remove", "days": ["2026-09-02"]}, db_path=db))
        assert out["deleted"] == 1 and out["summary"][0]["days"] == 2
