"""AIS-305: run_job with a collector → compose-only agent, journal write, cost line, silent skip."""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

from cron import brief_collector as bc
from cron.brief_sources.base import SourceStatus
from tests.cron._run_job_stubs import FakeAgent, install_run_job_stubs

JOB = {
    "id": "aimds-morning-brief", "name": "Morning Brief", "prompt": "Compose the brief.", "deliver": "local",
    "origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"},
}


def _fake_collect(kind="morning-brief", has_new=True, text="## Collected Data\n### Calendar today\n- 09:00 Daily\n"):
    def _collect(job, k, cfg=None, **kw):
        from datetime import datetime, timezone

        w = bc.build_window(k, datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), {})
        res = bc.CollectorResult(kind=k, text=text if has_new else "", sources=[SourceStatus("m365", "active", "3")],
                                 window=w, lang=bc.brief_language(cfg), has_new=has_new)
        return res
    return _collect


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    install_run_job_stubs(monkeypatch, home, config={"display": {"language": "de"}}, vault=vault, keep_prompt_builder=True)
    monkeypatch.setattr(bc, "collect", _fake_collect())
    FakeAgent.response = "# Tages-Briefing\n\n## 📅 Heute\n- 09:00 Daily\n\nFINDING: Daily um 9\nNEXT: Vorbereiten"
    FakeAgent.tool_messages = 0
    return home, vault


def test_compose_only_run_has_no_tools_and_writes_journal(env, caplog):
    home, vault = env
    from cron import scheduler

    caplog.set_level(logging.INFO)
    ok, output, response, err = scheduler.run_job(dict(JOB))
    assert ok, err
    agent = FakeAgent.instances[-1]
    assert agent.enabled_toolsets == []
    assert agent.max_iterations == 3
    prompt = agent.prompt_received
    assert "## Collected Data" in prompt and "You have NO tools" in prompt
    assert prompt.count("LANGUAGE:") == 1 and "in German" in prompt
    assert "TOOLS: If `tool_search`" not in prompt

    journal = vault / "journal" / "2026-09-08-morning-brief.md"
    assert journal.exists()
    text = journal.read_text(encoding="utf-8")
    fm = text.split("---")[1]
    keys = [ln.split(":")[0] for ln in fm.strip().splitlines()]
    assert keys == ["type", "title", "created", "updated", "status", "tags", "related_to"]
    assert "type: journal" in fm and 'title: "Tages-Briefing 2026-09-08"' in fm and "tags: [journal, morning-brief]" in fm
    assert "## Heute" in text and "📅" not in text.split("## Heute")[0][-5:]
    assert "FINDING: Daily um 9" in text
    assert len(list((vault / "journal").glob("*.md"))) == 2  # brief + _hub.md
    hub = (vault / "journal" / "_hub.md").read_text(encoding="utf-8")
    assert "<!-- briefs:start -->" in hub and "[[journal/2026-09-08-morning-brief|Tages-Briefing 2026-09-08]]" in hub

    assert f"**Journal:** {journal}" in output and "**Collector:** Sources: m365=active(3)" in output
    assert "**Cost:** api_calls=1 in=1234 out=56 cache_read=100" in output
    assert any("[AIS-161] cron cost: job=aimds-morning-brief api_calls=1 in=1234" in r.getMessage() for r in caplog.records)
    meta = scheduler.pop_run_meta("aimds-morning-brief")
    assert meta["journal_path"] == str(journal) and meta["session_id"].startswith("cron_aimds-morning-brief_")


def test_journal_rerun_preserves_created_and_replaces_hub_block(env):
    home, vault = env
    from cron import scheduler

    (vault / "journal").mkdir()
    (vault / "journal" / "2026-09-08-morning-brief.md").write_text("---\ntype: journal\ncreated: 2026-09-01\n---\n# old\n", encoding="utf-8")
    (vault / "journal" / "_hub.md").write_text("# Journal\n\nintro\n\n<!-- briefs:start -->\nold\n<!-- briefs:end -->\n\ntail\n", encoding="utf-8")
    ok, *_ = scheduler.run_job(dict(JOB))
    assert ok
    text = (vault / "journal" / "2026-09-08-morning-brief.md").read_text(encoding="utf-8")
    assert "created: 2026-09-01" in text and "updated: 2026-09-08" in text and "# old" not in text
    hub = (vault / "journal" / "_hub.md").read_text(encoding="utf-8")
    assert hub.startswith("# Journal\n\nintro\n\n<!-- briefs:start -->") and hub.endswith("<!-- briefs:end -->\n\ntail\n")
    assert "old\n" not in hub.split("<!-- briefs:start -->")[1]


def test_silent_response_writes_no_journal(env):
    home, vault = env
    from cron import scheduler

    FakeAgent.response = "[SILENT]"
    ok, output, response, err = scheduler.run_job(dict(JOB))
    assert ok and response == "[SILENT]"
    assert not (vault / "journal").exists()


def test_collector_exception_falls_back_to_legacy_run(env, monkeypatch):
    home, vault = env
    from cron import scheduler

    def _boom(*a, **k):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(bc, "collect", _boom)
    monkeypatch.setattr(scheduler, "_resolve_cron_enabled_toolsets", lambda job, cfg: ["hermes-cron"])
    ok, *_ = scheduler.run_job(dict(JOB))
    assert ok
    agent = FakeAgent.instances[-1]
    assert agent.enabled_toolsets == ["hermes-cron"] and agent.max_iterations == 90
    assert "## Collected Data" not in agent.prompt_received and "LANGUAGE:" not in agent.prompt_received


def test_non_collector_job_is_untouched(env, monkeypatch):
    from cron import scheduler

    monkeypatch.setattr(scheduler, "_resolve_cron_enabled_toolsets", lambda job, cfg: None)
    ok, *_ = scheduler.run_job({"id": "user-job", "name": "Mine", "prompt": "hello", "deliver": "local"})
    assert ok
    agent = FakeAgent.instances[-1]
    assert agent.enabled_toolsets is None and "Collected Data" not in agent.prompt_received


def test_nothing_new_skips_agent_entirely(env, monkeypatch, caplog):
    from cron import scheduler

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(bc, "collect", _fake_collect(kind="mail-check", has_new=False))
    job = {"id": "aimds-m365-mail-check", "name": "M365 Mail Check", "prompt": "Summarize.", "deliver": "local",
           "origin": {"source": "aimds-default-cron", "seed_key": "m365-mail-check"}}
    ok, output, response, err = scheduler.run_job(job)
    assert ok and response == "[SILENT]" and err is None
    assert FakeAgent.instances == []
    assert "**Status:** silent (collector: nothing new)" in output and "**Collector:** Sources: m365=active(3)" in output
    assert any("cron cost: job=aimds-m365-mail-check api_calls=0" in r.getMessage() for r in caplog.records)
    assert scheduler.pop_run_meta("aimds-m365-mail-check")["silent"] is True


def test_process_job_records_output_path_and_summary(env, monkeypatch):
    """The ticker path stores last_output_path/last_output_at/summary on the job."""
    home, vault = env
    from cron import jobs as cron_jobs
    from cron import scheduler

    created = cron_jobs.create_job(prompt="Compose the brief.", schedule="0 8 * * 1-5", name="Morning Brief")
    cron_jobs.update_job(created["id"], {"origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}})
    job = cron_jobs.get_job(created["id"])
    monkeypatch.setattr(scheduler, "get_due_jobs", lambda: [job])
    monkeypatch.setattr(scheduler, "advance_next_run", lambda *a, **k: None, raising=False)
    events = []
    cron_jobs.register_global_completion_callback(lambda jid, ok, err, **extra: events.append((jid, ok, extra)))
    scheduler.tick(verbose=False, sync=True)
    saved = cron_jobs.get_job(created["id"])
    assert saved["last_status"] == "ok"
    assert saved["last_output_path"].endswith("journal/2026-09-08-morning-brief.md")
    assert saved["last_output_at"] and saved["last_run_session_id"].startswith("cron_")
    assert saved["last_output_summary"] == {"finding": "Daily um 9", "next": "Vorbereiten", "open_question": ""}
    assert events and events[-1][2]["output_path"] == saved["last_output_path"] and events[-1][2]["job_name"] == "Morning Brief"
    # mark seen
    cron_jobs.mark_job_seen(created["id"])
    assert cron_jobs.get_job(created["id"])["last_seen_at"] >= saved["last_output_at"]
