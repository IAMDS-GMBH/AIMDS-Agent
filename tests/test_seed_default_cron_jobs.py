from __future__ import annotations

import importlib.util
import json
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


def _read_jobs(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("jobs", [])


def test_seeds_defaults_once(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()

    result = seed_default_cron_jobs(home)
    assert result["status"] == "seeded"
    assert result["added"] == "2"
    jobs = _read_jobs(home / "cron" / "jobs.json")
    seed_keys = {
        job.get("origin", {}).get("seed_key")
        for job in jobs
        if job.get("origin", {}).get("source") == "aimds-default-cron"
    }
    assert seed_keys == {"morning-brief", "weekly-review"}
    assert (home / ".aimds-default-cron-seeded").is_file()

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
    assert result["added"] == "1"
    assert result["skipped_existing"] == "1"

    jobs = _read_jobs(home / "cron" / "jobs.json")
    weekly_jobs = [
        job for job in jobs
        if str(job.get("id", "")).startswith("weekly")
        or job.get("origin", {}).get("seed_key") in {"weekly-digest", "weekly-review"}
    ]
    assert len(weekly_jobs) == 1
