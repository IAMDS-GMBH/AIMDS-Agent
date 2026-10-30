"""AIS-797: the /notes and /today developer probes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cli import HermesCLI
from hermes_cli.commands import resolve_command

GET = "mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package"


@pytest.fixture
def db(tmp_path):
    from hermes_state import SessionDB

    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session(session_id="cli-session", source="cli")
    yield database
    database.close()


def _cli(db, agent=None):
    return SimpleNamespace(_session_db=db, session_id="cli-session", agent=agent)


def test_commands_are_cli_only_probes():
    for name in ("notes", "today"):
        cmd = resolve_command(name)
        assert cmd is not None and cmd.cli_only and cmd.category == "Info"
    assert resolve_command("today").subcommands == ("refresh",)


def test_notes_prints_the_ledger(db, capsys):
    from agent.session_ledger import record_tool_call

    agent = SimpleNamespace(session_id="cli-session", _session_db=db, _current_turn_id="t1")
    record_tool_call(agent, GET, {"id": "DEMO-1"},
                     json.dumps({"result": {"id": 9001, "key": "DEMO-1", "subject": "Check backups", "status": "Backlog"}}),
                     tool_call_id="c1")
    HermesCLI._show_session_notes(_cli(db, agent))
    out = capsys.readouterr().out
    assert "lineage cli-session, 1 entries" in out
    assert "read" in out and "DEMO-1" in out and "Check backups" in out and "[Backlog]" in out


def test_notes_without_entries_says_so(db, capsys):
    HermesCLI._show_session_notes(_cli(db))
    assert "Nothing in the ledger yet" in capsys.readouterr().out


def test_today_prints_the_feed_and_passes_refresh(monkeypatch, capsys):
    from cron import workspace_feed as wf

    asked = []
    feed = {"v": 1, "generated_at": "2026-10-30T09:00:00+01:00", "cached": True, "age_s": 12.0,
            "window": {"kind": "workspace", "today": "2026-10-30", "preview_day": "2026-11-02",
                       "start": "", "end": ""},
            "items": [{"id": "openproject:ticket:DEMO-1", "source": "openproject", "kind": "ticket", "key": "DEMO-1",
                       "title": "Check backups", "status": "In Progress", "prev_status": "Backlog", "mine": True,
                       "due_at": "2026-11-06"}],
            "sources": [{"name": "openproject", "label": "OpenProject", "status": "active", "detail": "1",
                         "count": 1, "partial": False, "unavailable": False}]}
    monkeypatch.setattr(wf, "get_feed", lambda refresh=False: asked.append(refresh) or feed)
    HermesCLI._show_work_feed(SimpleNamespace(), "/today refresh")
    out = capsys.readouterr().out
    assert asked == [True]
    assert "Work feed 2026-10-30" in out and "Sources: openproject=active(1)" in out
    assert "DEMO-1 Check backups [Backlog → In Progress]" in out
