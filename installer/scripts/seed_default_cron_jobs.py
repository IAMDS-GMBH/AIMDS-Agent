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


SEED_STATE_FILE_REL = Path("state") / "default_cron_seed.json"
# Legacy marker kept only for backward-compatible migration.
LEGACY_MARKER_FILE = ".aimds-default-cron-seeded"
# Version 1: initial default seeding gate.
# Version 2: update weekly-review prompt to explicitly include next-week planning.
# Version 3: enforce planning-oriented weekly output contract with concise sections.
# Version 4: weekly review contract includes explicit stale-project section.
# Version 5: weekly review contract adds OPEN_QUESTION_NEEDED marker requirement.
# Version 6: M365 integration, language awareness, preview notes, and work week boundaries.
# Version 7: Add M365 Mail Check (every 2 hours) and Teams Check (every 15 mins).
# Version 8: Add Vault & Memory Curator (daily at 3:00 AM) for automated vault maintenance & re-indexing.
# Version 9: Change Vault & Memory Curator to weekly at midday (Fridays at 12:00 PM).
# Version 10: briefs end with a FINDING/NEXT/OPEN_QUESTION marker block and land in
#             journal/ instead of _inbox/; curator archives stale _inbox entries.
CURRENT_DEFAULT_CRON_VERSION = 10
JOBS_FILE_REL = Path("cron") / "jobs.json"
_SOURCE = "aimds-default-cron"


_DEFAULT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "seed_key": "morning-brief",
        "name": "Morning Brief",
        "schedule": "0 8 * * 1-5",
        "prompt": (
            "Create the morning brief using digest and available context in the user's language (German or English). "
            "If MSOffice365MCP is active/connected, query Outlook calendar appointments, unread/actionable emails, and Teams updates. "
            "Restrict the schedule/task focus strictly to the current work week (Mon-Fri) or over weekends to the next working day (Monday). "
            "Write the full brief, including the preview for tomorrow/next working day, to the workspace file journal/YYYY-MM-DD-morning-brief.md "
            "(today's date; overwrite if it exists) so queries like 'what is scheduled for tomorrow?' can be answered from it. Do not create files under _inbox/. "
            "Finish your response with exactly this block on separate lines: 'FINDING: <one line - the single most important thing noticed>', 'NEXT: <one line - the recommended next action>', and, only if a decision or input from the user is missing, 'OPEN_QUESTION: <one line>'. Never leave the FINDING line out."
        ),
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
    {
        "seed_key": "weekly-review",
        "name": "Weekly Review",
        "schedule": "0 16 * * 5",
        "prompt": (
            "Create a concise weekly review in the user's language (German or English) in this exact structure: "
            "1) Key outcomes this week, "
            "2) Carry-over items, "
            "3) Next week top 3 priorities, "
            "4) Stale active projects (>=14 days inactivity), "
            "5) Risks/open questions needing decisions. "
            "If MSOffice365MCP is active/connected, include relevant emails, meetings, and team updates from the current work week. "
            "Restrict focus strictly to the current work week (and next working day over weekends). "
            "Write the full review to the workspace file journal/YYYY-MM-DD-weekly-review.md (today's date; overwrite if it exists). Do not create files under _inbox/. "
            "Finish your response with exactly this block on separate lines: 'FINDING: <one line - the single most important thing noticed>', 'NEXT: <one line - the recommended next action>', and, only if a decision or input from the user is missing, 'OPEN_QUESTION: <one line>'. Never leave the FINDING line out."
        ),
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
    {
        "seed_key": "m365-mail-check",
        "name": "M365 Mail Check",
        "schedule": "0 */2 * * 1-5",
        "prompt": (
            "Check Outlook inbox for new unread or actionable emails using MSOffice365MCP/email tools. "
            "If there are no new unread or actionable emails, report 'nothing new'. "
            "If new actionable customer emails arrive, summarize them compactly in the user's language and highlight required actions."
        ),
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
    {
        "seed_key": "m365-teams-check",
        "name": "M365 Teams Check",
        "schedule": "*/15 * * * 1-5",
        "prompt": (
            "Check Teams Activity Feed and chat messages for new unread mentions or messages using MSOffice365MCP/Teams tools. "
            "If there are no new unread mentions or messages, report 'nothing new'. "
            "If new messages or mentions exist, summarize them compactly in the user's language and highlight required actions."
        ),
        "skill": "digest",
        "deliver": "local",
        "enabled": True,
    },
    {
        "seed_key": "vault-curator",
        "name": "Vault & Memory Curator",
        "schedule": "0 12 * * 5",
        "prompt": (
            "Perform periodic background maintenance on the Vault (AIMDS-Suite-Vault) and memories:\n"
            "1) Clean up any stray HermesMemory symlinks/junctions in Vault or legacy Documents locations.\n"
            "2) Migrate any loose project or user files from ~/.hermes/memories/ into AIMDS-Suite-Vault/projects/ or /users/.\n"
            "3) Ensure Vault notes follow a clean 3–4 level deep hierarchy (projects/<bereich>/<projekt>/<thema>.md, users/<user>/<kategorie>/<thema>.md, notes/<bereich>/<jahr_monat>/<thema>.md).\n"
            "4) Clean up stale backup directories (HermesMemory.backup.*) and broken links.\n"
            "5) Trigger incremental vault re-indexing so hybrid search recall remains fast and up to date.\n"
            "6) Move _inbox/ entries older than 7 days into _inbox/_archive/ (never delete)."
        ),
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


def _job_is_aimds_default(job: dict[str, Any]) -> bool:
    origin = job.get("origin")
    return isinstance(origin, dict) and str(origin.get("source") or "").strip() == _SOURCE


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
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seed_key = _canonical_seed_key(spec["seed_key"])
    enabled = bool(spec.get("enabled", True))
    schedule_text = str(spec["schedule"]).strip()
    try:
        # Best path: reuse runtime parser so persisted schedule shape matches
        # the cron subsystem exactly when available.
        from cron.jobs import HAS_CRONITER, compute_next_run, parse_schedule

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
    except Exception:
        # Installer fallback: if cron runtime isn't importable yet, still seed
        # valid jobs with a minimal schedule shape and let runtime compute next_run_at.
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


def _upgrade_jobs_for_version(
    jobs: list[dict[str, Any]], from_version: int, to_version: int
) -> int:
    updated = 0
    if from_version >= to_version:
        return 0

    # v2 migration: weekly review prompt clarifies next-week planning.
    if from_version < 2 <= to_version:
        weekly_spec = next(
            (spec for spec in _DEFAULT_SPECS if _canonical_seed_key(spec["seed_key"]) == "weekly-review"),
            None,
        )
        if weekly_spec is not None:
            weekly_aliases = _aliases("weekly-review")
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, weekly_aliases):
                    continue
                target_prompt = str(weekly_spec.get("prompt") or "").strip()
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1
                if str(job.get("skill") or "").strip() != "digest":
                    job["skill"] = "digest"
                    updated += 1
                skills = job.get("skills")
                if not isinstance(skills, list) or "digest" not in [str(x) for x in skills]:
                    job["skills"] = ["digest"]
                    updated += 1

    # v3 migration: weekly review prompt must include planning sections and decisions.
    if from_version < 3 <= to_version:
        weekly_spec = next(
            (spec for spec in _DEFAULT_SPECS if _canonical_seed_key(spec["seed_key"]) == "weekly-review"),
            None,
        )
        if weekly_spec is not None:
            weekly_aliases = _aliases("weekly-review")
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, weekly_aliases):
                    continue
                target_prompt = str(weekly_spec.get("prompt") or "").strip()
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    # v4 migration: weekly review prompt includes explicit stale-project section.
    if from_version < 4 <= to_version:
        weekly_spec = next(
            (spec for spec in _DEFAULT_SPECS if _canonical_seed_key(spec["seed_key"]) == "weekly-review"),
            None,
        )
        if weekly_spec is not None:
            weekly_aliases = _aliases("weekly-review")
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, weekly_aliases):
                    continue
                target_prompt = str(weekly_spec.get("prompt") or "").strip()
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    # v5 migration: weekly review prompt requires OPEN_QUESTION_NEEDED marker.
    if from_version < 5 <= to_version:
        weekly_spec = next(
            (spec for spec in _DEFAULT_SPECS if _canonical_seed_key(spec["seed_key"]) == "weekly-review"),
            None,
        )
        if weekly_spec is not None:
            weekly_aliases = _aliases("weekly-review")
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, weekly_aliases):
                    continue
                target_prompt = str(weekly_spec.get("prompt") or "").strip()
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    # v6 migration: M365 integration, language awareness, preview notes, and work week boundaries.
    if from_version < 6 <= to_version:
        for spec in _DEFAULT_SPECS:
            s_key = _canonical_seed_key(spec["seed_key"])
            s_aliases = _aliases(s_key)
            target_prompt = str(spec.get("prompt") or "").strip()
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, s_aliases):
                    continue
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    # v7 migration: update M365 Mail Check and Teams Check prompts for existing jobs.
    if from_version < 7 <= to_version:
        for spec in _DEFAULT_SPECS:
            s_key = _canonical_seed_key(spec["seed_key"])
            s_aliases = _aliases(s_key)
            target_prompt = str(spec.get("prompt") or "").strip()
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, s_aliases):
                    continue
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    # v9 migration: update Vault & Memory Curator schedule to weekly midday (Fridays 12:00 PM).
    if from_version < 9 <= to_version:
        curator_spec = next(
            (spec for spec in _DEFAULT_SPECS if _canonical_seed_key(spec["seed_key"]) == "vault-curator"),
            None,
        )
        if curator_spec is not None:
            curator_aliases = _aliases("vault-curator")
            target_schedule = str(curator_spec.get("schedule") or "").strip()
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, curator_aliases):
                    continue
                sched = job.get("schedule")
                if isinstance(sched, dict) and sched.get("expr") != target_schedule:
                    sched["expr"] = target_schedule
                    sched["display"] = target_schedule
                    job["schedule_display"] = target_schedule
                    updated += 1

    # v10 migration: marker block + journal/ target for briefs, _inbox archiving for the curator.
    if from_version < 10 <= to_version:
        for key in ("morning-brief", "weekly-review", "vault-curator"):
            spec = next(
                (sp for sp in _DEFAULT_SPECS if _canonical_seed_key(sp["seed_key"]) == key),
                None,
            )
            if spec is None:
                continue
            target_prompt = str(spec.get("prompt") or "").strip()
            for job in jobs:
                if not _job_is_aimds_default(job):
                    continue
                if not _job_matches_alias(job, _aliases(key)):
                    continue
                if str(job.get("prompt") or "").strip() != target_prompt:
                    job["prompt"] = target_prompt
                    updated += 1

    return updated


def _read_seed_version(state_file: Path) -> int:
    if not state_file.is_file():
        return 0
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    raw = payload.get("seed_version", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _write_seed_state(state_file: Path, *, version: int, added: int, skipped_existing: int) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "seed_version": version,
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


def seed_default_cron_jobs(hermes_home: Path) -> dict[str, str]:
    seed_state_file = hermes_home / SEED_STATE_FILE_REL
    version = _read_seed_version(seed_state_file)
    if version >= CURRENT_DEFAULT_CRON_VERSION:
        return {"status": "already-seeded"}

    # Backward compatibility: migrate legacy marker installations into
    # versioned state without reseeding.
    legacy_marker = hermes_home / LEGACY_MARKER_FILE
    if version == 0 and legacy_marker.exists():
        _write_seed_state(
            seed_state_file,
            version=CURRENT_DEFAULT_CRON_VERSION,
            added=0,
            skipped_existing=0,
        )
        return {"status": "already-seeded"}

    jobs_file = hermes_home / JOBS_FILE_REL
    jobs = _read_jobs(jobs_file)

    # Upgrade path (versioned defaults evolution): update existing AIMDS-owned
    # jobs in place, but do not create missing defaults. If a user deleted a
    # default job, that remains user-owned behavior.
    if version > 0:
        updated = _upgrade_jobs_for_version(jobs, version, CURRENT_DEFAULT_CRON_VERSION)
        if updated:
            _write_jobs(jobs_file, jobs)
        _write_seed_state(
            seed_state_file,
            version=CURRENT_DEFAULT_CRON_VERSION,
            added=0,
            skipped_existing=0,
        )
        return {
            "status": "upgraded",
            "updated": str(updated),
        }

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

    _write_seed_state(
        seed_state_file,
        version=CURRENT_DEFAULT_CRON_VERSION,
        added=added,
        skipped_existing=skipped_existing,
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
