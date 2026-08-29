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
        assert "configure" in out["then"]

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
        assert saved["title"] == wt.PROFILE_TITLE and saved["type"] == "profile" and "worktime" in saved["tags"]
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
        assert out["range"]["inclusive"] is True and "Sollzeit_netto" in out["formula"]

    def test_workdays_with_days_and_six_day_week(self):
        out = _run(action="workdays", start="2026-08-01", end="2026-08-31", include_days=True, region="DE-BY", weekly_hours=48, days_per_week=6)
        assert out["totals"]["workdays_net"] == 25 and "target_hours" not in out["totals"]
        sat = next(d for d in out["days"] if d["day"] == "2026-08-15")
        assert sat["reason"] == "holiday" and sat["holiday"] == "Mariä Himmelfahrt"

    def test_invalid_input_is_a_tool_error(self):
        assert _run(action="holidays", year=2026, region="XX")["success"] is False
        assert _run(action="nonsense")["success"] is False
        assert _run(action="workdays", start="2026-02-01", end="2026-01-01", **BY)["success"] is False


class TestMaterialize:
    def test_table_joins_with_ingested_worklogs_without_multiplying(self, tmp_path):
        db = tmp_path / "state.db"
        out = json.loads(wt.execute_workdays({"action": "materialize", "start": "2026-01-01", "end": "2026-01-31", **BY}, db_path=db))
        assert out["table"] == "workday_calendar" and out["rows"] == 31 and out["totals"]["target_hours"] == 160.0

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
        assert "| 2026-01 | 160.0 | 3.0 |" in res

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
