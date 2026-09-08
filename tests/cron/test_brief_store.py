"""AIS-305: brief_items persistence — upsert, status diff, watermark, prune."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cron.brief_sources.base import BriefItem
from cron.brief_store import BriefStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    s = BriefStore(tmp_path / "state.db")
    yield s
    s.close()


def _ticket(key, status, category="In Progress"):
    return BriefItem(source="jira", kind="ticket", key=key, title=f"T {key}", status=status,
                     extra={"category": category})


def test_upsert_reports_new_and_status_changes(store):
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    res = store.upsert_items([_ticket("AIS-1", "To Do"), _ticket("AIS-2", "In Progress")], now)
    assert {i.key for i in res["new"]} == {"AIS-1", "AIS-2"} and res["changed"] == []

    later = now + timedelta(days=1)
    res = store.upsert_items([_ticket("AIS-1", "Done", "Done"), _ticket("AIS-2", "In Progress")], later)
    assert res["new"] == []
    assert [(i.key, prev) for i, prev in res["changed"]] == [("AIS-1", "To Do")]

    changed = store.changed_since(now + timedelta(hours=1))
    assert [c.key for c in changed] == ["AIS-1"]
    assert changed[0].prev_status == "To Do" and changed[0].status == "Done"
    # first_seen preserved, last_seen advanced
    items = {i.key: i for i in store.items(source="jira")}
    assert items["AIS-1"].first_seen_at.startswith("2026-09-08")


def test_open_ticket_keys_excludes_done_and_is_bounded(store):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    store.upsert_items([_ticket(f"AIS-{n}", "To Do") for n in range(15)] + [_ticket("AIS-99", "Done", "Done")], now)
    keys = store.open_ticket_keys("jira", limit=10)
    assert len(keys) == 10 and "AIS-99" not in keys


def test_existing_ids_is_the_mail_watermark(store):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    mails = [BriefItem(source="m365", kind="mail", key=f"m{n}", title="x", status="unread") for n in range(3)]
    assert store.existing_ids(i.id for i in mails) == set()
    store.upsert_items(mails[:2], now)
    assert store.existing_ids(i.id for i in mails) == {mails[0].id, mails[1].id}


def test_prune_removes_old_items_and_runs(store):
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.upsert_items([_ticket("OLD-1", "To Do")], old)
    store.record_run("job", "morning-brief", "Sources: -", {}, old)
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    store.upsert_items([_ticket("NEW-1", "To Do")], now)
    store.record_run("job", "morning-brief", "Sources: -", {}, now)
    removed = store.prune(now)
    assert removed == 2
    assert [i.key for i in store.items()] == ["NEW-1"]
    assert store.last_run_at("job") == now


def test_target_hours_reads_workday_calendar_when_present(store):
    assert store.target_hours("2026-09-08") is None
    store._conn.execute("CREATE TABLE workday_calendar (day TEXT PRIMARY KEY, target_hours REAL)")
    store._conn.execute("INSERT INTO workday_calendar VALUES ('2026-09-08', 8.0)")
    store._conn.commit()
    assert store.target_hours("2026-09-08") == 8.0
