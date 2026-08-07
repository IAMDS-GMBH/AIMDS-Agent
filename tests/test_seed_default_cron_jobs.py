from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "installer"
    / "scripts"
    / "seed_default_cron_jobs.py"
)
_SPEC = importlib.util.spec_from_file_location("seed_default_cron_jobs", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

seed_default_cron_jobs = _MODULE.seed_default_cron_jobs
SEED_STATE_FILE_REL = _MODULE.SEED_STATE_FILE_REL
CURRENT_DEFAULT_CRON_VERSION = _MODULE.CURRENT_DEFAULT_CRON_VERSION


def _read_jobs(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("jobs", [])


def test_seeds_defaults_once(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"
    assert result["added"] == "5"
    jobs = _read_jobs(home / "cron" / "jobs.json")
    seed_keys = {
        job.get("origin", {}).get("seed_key")
        for job in jobs
        if job.get("origin", {}).get("source") == "aimds-default-cron"
    }
    assert seed_keys == {"morning-brief", "weekly-review", "m365-mail-check", "m365-teams-check", "vault-curator"}
    state_file = home / SEED_STATE_FILE_REL
    assert state_file.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state.get("seed_version") == CURRENT_DEFAULT_CRON_VERSION

    second = seed_default_cron_jobs(home)
    assert second["status"] == "already-seeded"
    jobs_after = _read_jobs(home / "cron" / "jobs.json")
    assert len(jobs_after) == len(jobs)


def test_respects_existing_weekly_digest_alias(tmp_path):
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    existing = {
        "jobs": [
            {
                "id": "weekly-digest",
                "name": "Weekly Digest",
                "schedule": {"kind": "cron", "expr": "0 16 * * 5", "display": "0 16 * * 5"},
                "enabled": True,
            }
        ]
    }
    (home / "cron" / "jobs.json").write_text(json.dumps(existing), encoding="utf-8")

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"
    assert result["added"] == "4"
    assert result["skipped_existing"] == "1"

    jobs = _read_jobs(home / "cron" / "jobs.json")
    weekly_jobs = [
        job for job in jobs
        if str(job.get("id", "")).startswith("weekly")
        or job.get("origin", {}).get("seed_key") in {"weekly-digest", "weekly-review"}
    ]
    assert len(weekly_jobs) == 1


def test_seeds_when_cron_runtime_import_is_unavailable(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()

    # Simulate installer context where cron.jobs import path is unavailable.
    monkeypatch.setitem(sys.modules, "cron", None)
    monkeypatch.setitem(sys.modules, "cron.jobs", None)

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"
    assert result["added"] == "5"
    assert (home / SEED_STATE_FILE_REL).is_file()

    jobs = _read_jobs(home / "cron" / "jobs.json")
    assert len(jobs) == 5
    for job in jobs:
        assert job["schedule"]["kind"] == "cron"
        assert "expr" in job["schedule"]


def test_migrates_legacy_marker_to_seed_state_without_reseeding(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    legacy_marker = home / ".aimds-default-cron-seeded"
    legacy_marker.write_text("legacy\n", encoding="utf-8")

    result = seed_default_cron_jobs(home)
    assert result["status"] == "already-seeded"

    state_file = home / SEED_STATE_FILE_REL
    assert state_file.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state.get("seed_version") == CURRENT_DEFAULT_CRON_VERSION
    assert not (home / "cron" / "jobs.json").exists()


def test_upgrade_updates_existing_weekly_default_without_recreating_missing(tmp_path):
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    jobs_path = home / "cron" / "jobs.json"
    jobs_payload = {
        "jobs": [
            {
                "id": "aimds-weekly-review",
                "name": "Weekly Review",
                "prompt": "Old weekly prompt",
                "skill": "digest",
                "skills": ["digest"],
                "origin": {"source": "aimds-default-cron", "seed_key": "weekly-review"},
                "schedule": {"kind": "cron", "expr": "0 16 * * 5", "display": "0 16 * * 5"},
                "enabled": True,
            }
        ]
    }
    jobs_path.write_text(json.dumps(jobs_payload), encoding="utf-8")

    # Simulate already-seeded v2 install that needs v3 upgrade.
    state_file = home / SEED_STATE_FILE_REL
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"seed_version": 2, "source": "aimds-default-cron"}, indent=2) + "\n",
        encoding="utf-8",
    )

    result = seed_default_cron_jobs(home)
    assert result["status"] == "upgraded"

    jobs = _read_jobs(jobs_path)
    assert len(jobs) == 1
    prompt = jobs[0]["prompt"]
    assert "Carry-over items" in prompt
    assert "Next week top 3 priorities" in prompt
    assert "Stale active projects (>=14 days inactivity)" in prompt
    assert "Risks/open questions needing decisions" in prompt
    assert "OPEN_QUESTION_NEEDED:" in prompt

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state.get("seed_version") == CURRENT_DEFAULT_CRON_VERSION


def test_seeded_weekly_review_prompt_includes_planning_contract(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"

    jobs = _read_jobs(home / "cron" / "jobs.json")
    weekly = next(j for j in jobs if j.get("origin", {}).get("seed_key") == "weekly-review")
    prompt = str(weekly.get("prompt") or "")
    assert "Carry-over items" in prompt
    assert "Next week top 3 priorities" in prompt
    assert "Stale active projects (>=14 days inactivity)" in prompt
    assert "Risks/open questions needing decisions" in prompt
    assert "OPEN_QUESTION_NEEDED:" in prompt


def test_vault_curator_schedule_weekly_midday(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"

    jobs = _read_jobs(home / "cron" / "jobs.json")
    curator = next(j for j in jobs if j.get("origin", {}).get("seed_key") == "vault-curator")
    assert curator["schedule"]["expr"] == "0 12 * * 5"

