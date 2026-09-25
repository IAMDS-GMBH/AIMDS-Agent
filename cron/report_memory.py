"""Cron reports in the memory MCP, created once per user (AIS-429).

The morning brief and the weekly review are stored in memory automatically,
and a run first checks whether that report already exists there. The same
user often runs two clients (laptop + desktop); both schedule the same jobs,
and before this every report was created twice.

A report is identified by a deterministic key — the collector kind plus the
day (``morning-brief-2026-09-25``) or ISO week (``weekly-review-2026-W39``) —
carried as a memory tag, so the check does not depend on the title language.
Both clients start the same minute, so each waits a client-specific delay
(0–90 s, stable per machine) before it checks: the first one to finish its
check-and-create usually wins, and a re-check right before the save keeps a
late duplicate out of memory.

Everything here is best-effort: without a memory backend the job simply runs
as before.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import time
from datetime import date, datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REPORT_KINDS = ("morning-brief", "weekly-review")
REPORT_TAG = "cron-report"
_MAX_STAGGER_SECONDS = 90


def client_id() -> str:
    """Stable per machine and OS user, like the telemetry client id."""
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
        return f"{socket.gethostname()}-{user}"
    except Exception:
        return "unknown"


def report_key(kind: str, day: date) -> str:
    if kind == "weekly-review":
        year, week, _ = day.isocalendar()
        return f"{kind}-{year}-W{week:02d}"
    return f"{kind}-{day.isoformat()}"


def stagger_seconds(cid: Optional[str] = None) -> int:
    digest = hashlib.sha256((cid or client_id()).encode("utf-8")).digest()
    return digest[0] * _MAX_STAGGER_SECONDS // 255


def _facade():
    from agent.memory_facade import MODE_NONE, MemoryFacade

    facade = MemoryFacade.for_process()
    return None if facade.mode == MODE_NONE else facade


def find_existing(key: str, *, facade: Any = None) -> Optional[Dict[str, Any]]:
    """The stored report for *key*, or None (also when memory is unavailable)."""
    try:
        facade = facade or _facade()
        if facade is None:
            return None
        for hit in facade.search(f"tag:{key}", limit=5) or []:
            tags = hit.get("tags") or []
            text = " ".join(str(v) for v in (hit.get("title"), hit.get("slug"), hit.get("content")) if v)
            if key in tags or key in text:
                return hit
    except Exception as exc:
        logger.debug("report lookup for %s failed: %s", key, exc)
    return None


def wait_and_check(kind: str, day: date, *, sleep=time.sleep, facade: Any = None) -> Optional[Dict[str, Any]]:
    """Before a report run: stagger, then look for the report in memory."""
    if kind not in REPORT_KINDS:
        return None
    delay = stagger_seconds()
    if delay and not os.environ.get("PYTEST_CURRENT_TEST"):
        sleep(delay)
    return find_existing(report_key(kind, day), facade=facade)


def store(kind: str, day: date, title: str, content: str, *, facade: Any = None) -> bool:
    """After a report run: save it to memory unless another client just did."""
    if kind not in REPORT_KINDS or not str(content or "").strip():
        return False
    key = report_key(kind, day)
    try:
        facade = facade or _facade()
        if facade is None:
            return False
        if find_existing(key, facade=facade) is not None:
            logger.info("report %s already in memory (another client) — not stored twice", key)
            return False
        result = facade.save(
            title=title,
            content=f"{content.strip()}\n\n_report_key: {key} · client: {client_id()}_",
            type="report",
            tags=[REPORT_TAG, kind, key],
        )
        return bool(getattr(result, "ok", result))
    except Exception as exc:
        logger.debug("storing report %s failed: %s", key, exc)
        return False


__all__ = ["REPORT_KINDS", "client_id", "find_existing", "report_key", "stagger_seconds", "store", "wait_and_check"]
