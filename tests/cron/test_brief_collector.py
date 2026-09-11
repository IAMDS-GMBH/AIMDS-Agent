"""AIS-305: collector kind resolution, window/holiday logic, language, rendering, budget."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from cron import brief_collector as bc
from cron.brief_sources.base import BriefItem, SourceAdapter, SourceStatus
from cron.brief_store import BriefStore

TZ = ZoneInfo("Europe/Berlin")
HOLIDAYS = {date(2026, 12, 25): "1. Weihnachtstag", date(2026, 10, 3): "Tag der Deutschen Einheit"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    s = BriefStore(tmp_path / "state.db")
    yield s
    s.close()


# --- kind resolution -------------------------------------------------------
@pytest.mark.parametrize("job, expected", [
    ({"origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}}, "morning-brief"),
    ({"origin": {"source": "aimds-default-cron", "seed_key": "weekly-digest"}}, "weekly-review"),
    ({"origin": {"source": "aimds-default-cron", "seed_key": "m365-mail-check"}}, "mail-check"),
    ({"origin": {"source": "aimds-default-cron", "seed_key": "m365_teams_check"}}, "teams-check"),
    ({"origin": {"source": "aimds-default-cron", "seed_key": "vault-curator"}}, None),
    ({"collector": "morning-brief"}, "morning-brief"),
    ({"collector": "none", "origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}}, None),
    ({"collector": False, "origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}}, None),
    ({"name": "Morning Brief", "prompt": "morning brief please"}, None),
])
def test_resolve_collector_kind(job, expected):
    assert bc.resolve_collector_kind(job, {}) == expected


def test_resolve_collector_kind_respects_config_switch():
    job = {"origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}}
    assert bc.resolve_collector_kind(job, {"cron": {"brief_collector": {"enabled": False}}}) is None


# --- language ----------------------------------------------------------------
@pytest.mark.parametrize("cfg, expected", [
    ({}, "en"),
    ({"display": {"language": "de"}}, "de"),
    ({"display": {"language": "de-AT"}}, "de"),
    ({"display": {"language": "fr"}}, "fr"),
    ({"display": {"language": "Deutsch"}}, "de"),
    ({"display": {"language": "xx"}}, "en"),
    ({"display": {"language": ""}}, "en"),
    ({"display": {"language": "de"}, "cron": {"brief_collector": {"language": "en"}}}, "en"),
    ({"cron": {"brief_collector": {"language": "DE"}}}, "de"),
    ({"cron": {"brief_collector": {"language": "français"}}}, "fr"),
])
def test_brief_language(cfg, expected):
    assert bc.brief_language(cfg) == expected


def test_language_instruction_and_titles():
    de = bc.language_instruction("de")
    assert "in German" in de and "do not mix languages" in de
    assert "in French" in bc.language_instruction("fr")
    assert "in English" in bc.language_instruction("xx")
    # The scheduler parses these literally — the directive must tell the model to keep them.
    for marker in ("FINDING:", "NEXT:", "OPEN_QUESTION:", "[SILENT]"):
        assert marker in de
    assert bc.brief_title("morning-brief", "fr", date(2026, 9, 8)) == "Morning Brief 2026-09-08"
    assert bc.brief_title("morning-brief", "de", date(2026, 9, 8)) == "Tages-Briefing 2026-09-08"
    assert bc.brief_title("weekly-review", "en", date(2026, 9, 11)) == "Weekly Review 2026-W37"


# --- window ------------------------------------------------------------------
@pytest.mark.parametrize("day, preview, last_wd", [
    (date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 4)),    # Monday → Tue, last = Friday
    (date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 8)),   # Wednesday
    (date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 10)), # Friday → Monday
    (date(2026, 9, 12), date(2026, 9, 14), date(2026, 9, 11)), # Saturday → Monday
    (date(2026, 12, 24), date(2026, 12, 28), date(2026, 12, 23)),  # Thu before Xmas (Fri holiday) → Mon
    (date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 1)), # Fri before 3 Oct (Sat) → Mon
])
def test_morning_window(day, preview, last_wd):
    now = datetime.combine(day, datetime.min.time(), tzinfo=TZ).replace(hour=8)
    w = bc.build_window("morning-brief", now, HOLIDAYS)
    assert (w.today, w.preview_day, w.last_working_day) == (day, preview, last_wd)
    assert w.start.date() == day and w.end.date() == preview and w.start.tzinfo is TZ


def test_weekly_window_spans_iso_week():
    now = datetime(2026, 9, 11, 16, 0, tzinfo=TZ)
    w = bc.build_window("weekly-review", now, HOLIDAYS)
    assert (w.week_start, w.week_end, w.preview_day) == (date(2026, 9, 7), date(2026, 9, 11), date(2026, 9, 14))
    assert w.start.date() == date(2026, 9, 7) and w.end.date() == date(2026, 9, 11)


# --- collect with fake adapters -------------------------------------------
class _Adapter(SourceAdapter):
    def __init__(self, name, items=None, skip=None, boom=False, kinds=()):
        self.name, self._items, self._skip, self._boom, self.kinds = name, items or [], skip, boom, kinds

    def availability(self, ctx):
        return self._skip

    def fetch(self, window, ctx):
        if self._boom:
            raise RuntimeError("upstream 503")
        return list(self._items)


def _now():
    return datetime(2026, 9, 8, 8, 0, tzinfo=TZ)


def test_collect_sources_line_and_sections(store):
    items = [
        BriefItem("m365", "event", "e1", "Daily", starts_at="2026-09-08T08:45+02:00", ends_at="2026-09-08T08:50+02:00", extra={"location": "Teams"}),
        BriefItem("m365", "event", "e2", "Planning", starts_at="2026-09-09T10:00+02:00", ends_at="2026-09-09T11:00+02:00"),
        BriefItem("jira", "ticket", "AIS-305", "Brief lean", status="In Progress", priority="High", due_at="2026-09-10", extra={"category": "In Progress"}),
        BriefItem("m365", "mail", "m1", "Invoice", status="unread", updated_at="2026-09-08T07:10+02:00", extra={"from": "Anna"}),
        BriefItem("tempo", "worklog", "2026-09-07", "2 h logged", status="partial", extra={"hours": 2.0, "target_hours": 8.0, "by_issue": {"AIS-1": 2.0}}),
    ]
    adapters = [
        _Adapter("m365", [i for i in items if i.source == "m365"]),
        _Adapter("jira", [i for i in items if i.source == "jira"]),
        _Adapter("tempo", [i for i in items if i.source == "tempo"]),
        _Adapter("workspace", skip=SourceStatus("workspace", "skipped", "no workspace")),
        _Adapter("openproject", boom=True),
    ]
    res = bc.collect({"id": "aimds-morning-brief"}, "morning-brief", now=_now(), cfg={}, store=store,
                     adapters=adapters, mcp_status=[], tool_names=[], vault_root=None, holidays=HOLIDAYS)
    line = res.sources_line()
    assert "m365=active(3)" in line and "jira=active(1)" in line and "tempo=active(1)" in line
    assert "workspace=skipped(no workspace)" in line and "openproject=failed(upstream 503)" in line
    assert "### Calendar today\n- 08:45–08:50  Daily (Teams)" in res.text
    assert "### Calendar 2026-09-09 (preview)\n- 10:00–11:00  Planning" in res.text
    assert "- AIS-305 Brief lean [In Progress, High, due 2026-09-10]" in res.text
    assert "| Anna | Invoice" in res.text
    assert "2026-09-07: 2 h logged of 8 h target (partial) — AIS-1 2h" in res.text
    assert "### Teams\n(none)" in res.text
    assert res.compose_only and res.has_new and res.counts == {"event": 2, "ticket": 1, "mail": 1, "worklog": 1}
    # persisted
    assert {i.key for i in store.items()} == {"e1", "e2", "AIS-305", "m1", "2026-09-07"}
    assert store.last_run_at("aimds-morning-brief") is not None


def test_collect_reports_status_changes_since_last_run(store):
    t1 = BriefItem("jira", "ticket", "AIS-1", "One", status="To Do", extra={"category": "To Do"})
    bc.collect({"id": "j"}, "morning-brief", now=_now(), cfg={}, store=store, adapters=[_Adapter("jira", [t1])],
               mcp_status=[], tool_names=[], vault_root=None, holidays={})
    t1b = BriefItem("jira", "ticket", "AIS-1", "One", status="Done", extra={"category": "Done"})
    later = datetime(2026, 9, 9, 8, 0, tzinfo=TZ)
    res = bc.collect({"id": "j"}, "morning-brief", now=later, cfg={}, store=store, adapters=[_Adapter("jira", [t1b])],
                     mcp_status=[], tool_names=[], vault_root=None, holidays={})
    assert "### Changed since the last brief\n- AIS-1: To Do → Done (ticket)" in res.text


def test_collect_fits_char_budget(store):
    many = [BriefItem("m365", "mail", f"m{n}", "Subject " * 12, status="unread", updated_at="2026-09-08T07:00+02:00", extra={"from": "X" * 30}) for n in range(80)]
    many += [BriefItem("jira", "ticket", f"AIS-{n}", "Ticket " * 10, status="To Do", extra={"category": "To Do"}) for n in range(40)]
    res = bc.collect({"id": "j"}, "morning-brief", now=_now(), cfg={"cron": {"brief_collector": {"char_budget": 3000}}}, store=store,
                     adapters=[_Adapter("m365", [i for i in many if i.source == "m365"]), _Adapter("jira", [i for i in many if i.source == "jira"])],
                     mcp_status=[], tool_names=[], vault_root=None, holidays={})
    assert len(res.text) <= 3000
    assert "more)" in res.text and res.text.rstrip().endswith(res.sources_line()) or "(truncated)" in res.text


def test_mail_check_watermark_and_first_run_baseline(store):
    mails = [BriefItem("m365", "mail", f"m{n}", f"Mail {n}", status="unread", updated_at=f"2026-09-08T07:{n:02d}+02:00", extra={"from": "A"}) for n in range(20)]
    res = bc.collect({"id": "aimds-m365-mail-check"}, "mail-check", now=_now(), cfg={}, store=store, adapters=[_Adapter("m365", mails)],
                     mcp_status=[], tool_names=[], vault_root=None, holidays={})
    assert res.has_new and len(res.items) == 15  # baseline caps the first report
    assert len(res.pending_items) == 20           # but everything becomes known after commit
    assert store.existing_ids(i.id for i in mails) == set()  # nothing committed yet
    res.commit(store)
    assert len(store.existing_ids(i.id for i in mails)) == 20

    # second run: same mails → nothing new → agent must be skipped
    res2 = bc.collect({"id": "aimds-m365-mail-check"}, "mail-check", now=_now(), cfg={}, store=store, adapters=[_Adapter("m365", mails)],
                      mcp_status=[], tool_names=[], vault_root=None, holidays={})
    assert res2.has_new is False and res2.text == "" and res2.pending_items == []

    # third run: one new mail → only that one is rendered
    extra = BriefItem("m365", "mail", "m99", "Brand new", status="unread", updated_at="2026-09-08T09:00+02:00", extra={"from": "B"})
    res3 = bc.collect({"id": "aimds-m365-mail-check"}, "mail-check", now=_now(), cfg={}, store=store, adapters=[_Adapter("m365", mails + [extra])],
                      mcp_status=[], tool_names=[], vault_root=None, holidays={})
    assert res3.has_new and [i.key for i in res3.items] == ["m99"] and "Brand new" in res3.text and "Mail 3" not in res3.text


def test_collect_never_raises_when_store_and_status_unavailable(monkeypatch):
    monkeypatch.setattr(bc, "BriefStore", None, raising=False)
    res = bc.collect({"id": "j"}, "morning-brief", now=_now(), cfg={}, store=None, adapters=[_Adapter("jira", boom=True)],
                     mcp_status=None, tool_names=None, vault_root=None, holidays={})
    assert "jira=failed(upstream 503)" in res.sources_line()
