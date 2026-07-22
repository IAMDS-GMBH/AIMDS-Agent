#!/usr/bin/env python3
"""Seed one-time AIMDS default cron jobs for a Hermes home.

Usage:
    python seed_default_cron_jobs.py <hermes_home>

Behavior:
1. Seed defaults only on first install/sync (marker file gate)
2. If user later deletes seeded jobs, they are NOT recreated
3. Respect existing/manual alias jobs and avoid duplicates
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MARKER_FILE = ".aimds-default-cron-seeded"
JOBS_FILE_REL = Path("cron") / "jobs.json"
_SOURCE = "aimds-default-cron"


_DEFAULT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "seed_key": "morning-brief",
        "name": "Morning Brief",
        "schedule": "0 8 * * 1-5",
        "prompt": "Create the morning brief using digest and related context.",
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
    {
        "seed_key": "weekly-review",
        "name": "Weekly Review",
        "schedule": "0 16 * * 5",
        "prompt": "Create the weekly digest/review and summarize key outcomes.",
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
)


def _canonical_seed_key(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("_", "-")
    if text == "weekly-digest":
        return "weekly-review"
    return text


def _aliases(seed_key: str) -> set[str]:
    canonical = _canonical_seed_key(seed_key)
    if canonical == "weekly-review":
        return {"weekly-review", "weekly-digest"}
    return {canonical}


def _job_alias(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    return _canonical_seed_key(text)


def _job_matches_alias(job: dict[str, Any], aliases: set[str]) -> bool:
    if _job_alias(job.get("id")) in aliases:
        return True
    if _job_alias(job.get("name")) in aliases:
        return True
    origin = job.get("origin")
    if isinstance(origin, dict) and _job_alias(origin.get("seed_key")) in aliases:
        return True
    return False


def _read_jobs(jobs_file: Path) -> list[dict[str, Any]]:
    if not jobs_file.is_file():
        return []
    payload = json.loads(jobs_file.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        jobs = payload.get("jobs", [])
    elif isinstance(payload, list):
        jobs = payload
    else:
        return []
    return [j for j in jobs if isinstance(j, dict)]


def _write_jobs(jobs_file: Path, jobs: list[dict[str, Any]]) -> None:
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jobs": jobs,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    jobs_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _unique_id(preferred: str, jobs: list[dict[str, Any]]) -> str:
    existing = {str(j.get("id") or "") for j in jobs}
    if preferred not in existing:
        return preferred
    suffix = 2
    while True:
        candidate = f"{preferred}-{suffix}"
        if candidate not in existing:
            return candidate
        suffix += 1


def _build_job(spec: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    from cron.jobs import HAS_CRONITER, compute_next_run, parse_schedule

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seed_key = _canonical_seed_key(spec["seed_key"])
    enabled = bool(spec.get("enabled", True))
    schedule_text = str(spec["schedule"]).strip()
    if HAS_CRONITER:
        parsed_schedule = parse_schedule(schedule_text)
        next_run_at = compute_next_run(parsed_schedule) if enabled else None
    else:
        parsed_schedule = {
            "kind": "cron",
            "expr": schedule_text,
            "display": schedule_text,
        }
        next_run_at = None
    skill = str(spec.get("skill") or "").strip() or None
    job = {
        "id": _unique_id(f"aimds-{seed_key}", jobs),
        "name": str(spec.get("name") or seed_key.replace("-", " ").title()),
        "prompt": str(spec.get("prompt") or ""),
        "skills": [skill] if skill else [],
        "skill": skill,
        "schedule": parsed_schedule,
        "schedule_display": parsed_schedule.get("display", schedule_text),
        "deliver": spec.get("deliver") or "local",
        "origin": {
            "source": _SOURCE,
            "seed_key": seed_key,
        },
        "created_at": now_iso,
        "repeat": {"times": spec.get("repeat"), "completed": 0},
        "enabled": enabled,
        "state": "scheduled" if enabled else "paused",
        "paused_at": None,
        "paused_reason": None,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "next_run_at": next_run_at,
    }
    if not enabled:
        job["paused_at"] = now_iso
        job["paused_reason"] = "Disabled by AIMDS default"
    return job


def seed_default_cron_jobs(hermes_home: Path) -> dict[str, str]:
    marker = hermes_home / MARKER_FILE
    if marker.exists():
        return {"status": "already-seeded"}

    jobs_file = hermes_home / JOBS_FILE_REL
    jobs = _read_jobs(jobs_file)
    added = 0
    skipped_existing = 0

    for spec in _DEFAULT_SPECS:
        seed_key = _canonical_seed_key(spec["seed_key"])
        aliases = _aliases(seed_key)
        if any(_job_matches_alias(job, aliases) for job in jobs):
            skipped_existing += 1
            continue
        jobs.append(_build_job(spec, jobs))
        added += 1

    if added:
        _write_jobs(jobs_file, jobs)

    marker.write_text(
        json.dumps(
            {
                "seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": _SOURCE,
                "added": added,
                "skipped_existing": skipped_existing,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "seeded",
        "added": str(added),
        "skipped_existing": str(skipped_existing),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0]} <hermes_home>", file=sys.stderr)
        return 1

    home = Path(argv[1]).expanduser()
    try:
        result = seed_default_cron_jobs(home)
    except Exception as exc:
        print(f"seed-error: {exc}", file=sys.stderr)
        return 2

    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
