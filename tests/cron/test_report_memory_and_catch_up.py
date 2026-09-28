"""AIS-429: missed report runs are caught up once; a report exists once per user."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cron import jobs as cron_jobs
from cron import report_memory as rm

TZ = ZoneInfo("Europe/Berlin")


def _job(expr, **extra):
    return {"id": "j", "schedule": {"kind": "cron", "expr": expr}, **extra}


@pytest.mark.skipif(not cron_jobs.HAS_CRONITER, reason="croniter missing")
class TestCatchUp:
    def test_daily_run_slept_through_is_caught_up_the_same_day(self):
        missed = datetime(2026, 9, 25, 8, 0, tzinfo=TZ)
        assert cron_jobs._catch_up_in_same_period(_job("0 8 * * 1-5"), missed, missed + timedelta(hours=5))

    def test_not_on_the_next_day(self):
        missed = datetime(2026, 9, 25, 8, 0, tzinfo=TZ)
        # Friday's brief on Saturday: the next occurrence (Monday) handles it
        assert not cron_jobs._catch_up_in_same_period(_job("0 8 * * 1-5"), missed, datetime(2026, 9, 26, 9, 0, tzinfo=TZ))

    def test_weekly_run_is_caught_up_within_the_iso_week(self):
        missed = datetime(2026, 9, 25, 16, 0, tzinfo=TZ)  # Friday 16:00
        assert cron_jobs._catch_up_in_same_period(_job("0 16 * * 5"), missed, datetime(2026, 9, 27, 10, 0, tzinfo=TZ))
        assert not cron_jobs._catch_up_in_same_period(_job("0 16 * * 5"), missed, datetime(2026, 9, 28, 10, 0, tzinfo=TZ))

    def test_sub_daily_jobs_and_opt_out_keep_fast_forwarding(self):
        missed = datetime(2026, 9, 25, 8, 0, tzinfo=TZ)
        assert not cron_jobs._catch_up_in_same_period(_job("0 */2 * * *"), missed, missed + timedelta(hours=1, minutes=30))
        assert not cron_jobs._catch_up_in_same_period(_job("0 8 * * *", catch_up=False), missed, missed + timedelta(hours=3))


@pytest.fixture()
def _jobs_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")

    def _at(now):
        monkeypatch.setattr(cron_jobs, "_hermes_now", lambda: now)

    return _at


def _stored_job(expr, next_run_at, **extra):
    return {"id": "brief", "name": "Morning Briefing", "prompt": "brief", "enabled": True,
            "state": "scheduled", "schedule": {"kind": "cron", "expr": expr},
            "next_run_at": next_run_at.isoformat(), **extra}


@pytest.mark.skipif(not cron_jobs.HAS_CRONITER, reason="croniter missing")
class TestLatestMissedOccurrence:
    """AIS-431: backend off Thu 16:00 - Mon 09:30, next_run_at still Thu 25.09 08:00."""

    STALE = datetime(2026, 9, 25, 8, 0, tzinfo=TZ)

    def test_todays_run_within_grace_runs_once(self, _jobs_store):
        now = datetime(2026, 9, 28, 9, 32, tzinfo=TZ)
        _jobs_store(now)
        cron_jobs.save_jobs([_stored_job("0 8 * * 1-5", self.STALE)])
        assert [j["id"] for j in cron_jobs.get_due_jobs()] == ["brief"]
        cron_jobs.mark_job_run("brief", True)
        assert cron_jobs.get_due_jobs() == []
        assert cron_jobs.load_jobs()[0]["next_run_at"].startswith("2026-09-29T08:00")

    def test_same_day_outside_grace_still_runs(self, _jobs_store):
        # 11:00 is past the 2 h grace, but AIS-429's same-day rule catches the
        # Monday 08:00 run up; Thursday and Friday stay skipped.
        _jobs_store(datetime(2026, 9, 28, 11, 0, tzinfo=TZ))
        cron_jobs.save_jobs([_stored_job("0 8 * * 1-5", self.STALE)])
        assert [j["id"] for j in cron_jobs.get_due_jobs()] == ["brief"]

    def test_opt_out_runs_only_within_grace(self, _jobs_store):
        cron_jobs.save_jobs([_stored_job("0 8 * * 1-5", self.STALE, catch_up=False)])
        _jobs_store(datetime(2026, 9, 28, 9, 32, tzinfo=TZ))
        assert [j["id"] for j in cron_jobs.get_due_jobs()] == ["brief"]
        cron_jobs.save_jobs([_stored_job("0 8 * * 1-5", self.STALE, catch_up=False)])
        _jobs_store(datetime(2026, 9, 28, 11, 0, tzinfo=TZ))
        assert cron_jobs.get_due_jobs() == []
        assert cron_jobs.load_jobs()[0]["next_run_at"].startswith("2026-09-29T08:00")

    def test_weekend_start_skips_the_missed_weekdays(self, _jobs_store):
        # Saturday: the newest occurrence is Friday's, a day old — skipped.
        _jobs_store(datetime(2026, 9, 26, 10, 0, tzinfo=TZ))
        cron_jobs.save_jobs([_stored_job("0 8 * * 1-5", self.STALE)])
        assert cron_jobs.get_due_jobs() == []
        assert cron_jobs.load_jobs()[0]["next_run_at"].startswith("2026-09-28T08:00")

    def test_sub_daily_job_still_fast_forwards(self, _jobs_store):
        _jobs_store(datetime(2026, 9, 28, 9, 32, tzinfo=TZ))
        cron_jobs.save_jobs([_stored_job("*/30 * * * *", self.STALE)])
        assert cron_jobs.get_due_jobs() == []
        assert cron_jobs.load_jobs()[0]["next_run_at"].startswith("2026-09-28T10:00")


def test_report_keys_are_stable_and_language_free():
    assert rm.report_key("morning-brief", date(2026, 9, 25)) == "morning-brief-2026-09-25"
    assert rm.report_key("weekly-review", date(2026, 9, 25)) == "weekly-review-2026-W39"
    assert 0 <= rm.stagger_seconds("laptop-jh") <= 90
    assert rm.stagger_seconds("laptop-jh") == rm.stagger_seconds("laptop-jh")


class _Facade:
    def __init__(self, hits=None):
        self.hits = list(hits or [])
        self.saved = []

    def search(self, query, limit=5):
        key = query.split("tag:", 1)[1]
        return [h for h in self.hits if key in (h.get("tags") or [])]

    def save(self, **kwargs):
        self.saved.append(kwargs)
        self.hits.append({"title": kwargs["title"], "tags": kwargs["tags"]})
        return SimpleNamespace(ok=True)


def test_a_report_is_stored_once_per_user():
    facade = _Facade()
    day = date(2026, 9, 25)
    assert rm.wait_and_check("morning-brief", day, sleep=lambda s: None, facade=facade) is None
    assert rm.store("morning-brief", day, "Tages-Briefing 2026-09-25", "Inhalt", facade=facade) is True
    saved = facade.saved[0]
    assert saved["tags"] == ["cron-report", "morning-brief", "morning-brief-2026-09-25"] and saved["type"] == "report"
    # the second client finds it and neither runs nor stores again
    assert rm.wait_and_check("morning-brief", day, sleep=lambda s: None, facade=facade) is not None
    assert rm.store("morning-brief", day, "Morning Brief 2026-09-25", "Inhalt", facade=facade) is False
    assert len(facade.saved) == 1


def test_other_jobs_and_missing_memory_are_left_alone():
    assert rm.wait_and_check("m365-mail-check", date(2026, 9, 25), sleep=lambda s: None, facade=_Facade()) is None
    assert rm.store("morning-brief", date(2026, 9, 25), "t", "", facade=_Facade()) is False
