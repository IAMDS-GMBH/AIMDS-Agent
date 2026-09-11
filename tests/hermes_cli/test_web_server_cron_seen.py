"""AIS-305: /seen, /output/latest, runs annotation and completion payload."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def isolated_profiles(tmp_path, monkeypatch):
    from hermes_cli import profiles

    default_home = tmp_path / ".hermes"
    profiles_root = default_home / "profiles"
    (default_home / "cron").mkdir(parents=True, exist_ok=True)
    (default_home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    from cron import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "CRON_DIR", default_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", default_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", default_home / "cron" / "output")
    monkeypatch.setattr(jobs_mod, "CRON_CACHE_DIR", default_home / "cron" / "cache", raising=False)
    return {"default": default_home}


@pytest.mark.asyncio
async def test_seen_endpoint_sets_last_seen_at(isolated_profiles):
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile("default", "create_job", prompt="brief", schedule="every 1h", name="Morning Brief")
    assert job.get("last_seen_at") is None
    seen = await web_server.mark_cron_job_seen(job["id"])
    assert seen["profile"] == "default" and seen["last_seen_at"]
    listed = await web_server.list_cron_jobs(profile="default")
    assert listed[0]["last_seen_at"] == seen["last_seen_at"]


@pytest.mark.asyncio
async def test_seen_endpoint_404_for_unknown_job(isolated_profiles):
    from fastapi import HTTPException
    from hermes_cli import web_server

    with pytest.raises(HTTPException) as exc:
        await web_server.mark_cron_job_seen("nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_latest_output_endpoint_reads_artifact(isolated_profiles, tmp_path):
    from cron import jobs as cron_jobs
    from fastapi import HTTPException
    from hermes_cli import web_server

    job = web_server._call_cron_for_profile("default", "create_job", prompt="brief", schedule="every 1h", name="Morning Brief")
    with pytest.raises(HTTPException) as exc:
        await web_server.get_cron_job_latest_output(job["id"])
    assert exc.value.status_code == 404

    artifact = tmp_path / "journal" / "2026-09-08-morning-brief.md"
    artifact.parent.mkdir()
    artifact.write_text("# Brief\n\nFINDING: x\nNEXT: y\n", encoding="utf-8")
    cron_jobs.mark_job_run(job["id"], True, None, output_path=str(artifact), session_id="cron_x_1",
                           summary={"finding": "x", "next": "y", "open_question": ""})
    payload = await web_server.get_cron_job_latest_output(job["id"])
    assert payload["path"] == str(artifact) and payload["content"].startswith("# Brief")
    assert payload["summary"] == {"finding": "x", "next": "y", "open_question": ""} and payload["session_id"] == "cron_x_1"
    stored = cron_jobs.get_job(job["id"])
    assert stored["last_output_path"] == str(artifact) and stored["last_output_at"] and stored["last_run_session_id"] == "cron_x_1"


def test_completion_callbacks_accept_both_signatures(isolated_profiles):
    from cron import jobs as cron_jobs

    legacy, modern = [], []
    cron_jobs.register_global_completion_callback(lambda jid, ok, err: legacy.append((jid, ok)))
    cron_jobs.register_global_completion_callback(lambda jid, ok, err, **extra: modern.append(extra))
    try:
        job = cron_jobs.create_job(prompt="p", schedule="every 1h", name="Weekly Review")
        cron_jobs.mark_job_run(job["id"], True, None, output_path="/tmp/x.md", session_id="cron_s")
    finally:
        cron_jobs._global_completion_callbacks.clear()
    assert legacy == [(job["id"], True)]
    assert modern[-1]["job_name"] == "Weekly Review" and modern[-1]["output_path"] == "/tmp/x.md" and modern[-1]["session_id"] == "cron_s"
