"""Agent usage counters for the client telemetry (AIS-796).

Whether delegation takes load off the main chat shows in two numbers: tool
calls of the chat itself vs. of its subagent runs, and context compressions
per hour per chat. ``SessionDB.agent_usage_counters`` computes them from
``state.db``; this module keeps a small snapshot of them in
``HERMES_HOME/telemetry/agent-usage.json`` so both telemetry senders — the
desktop (Node, no SQLite) and ``hermes support send-telemetry`` — can add it
to the payload they already send to the Support-Tool.

Counters only: numbers and agent definition names, never goals, messages,
tool arguments or results. The snapshot is refreshed at most every ten
minutes, at the end of a main-chat turn.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_RELPATH = Path("telemetry") / "agent-usage.json"
SNAPSHOT_MIN_INTERVAL_S = 600
WINDOW_DAYS = 7
SNAPSHOT_VERSION = 1


def snapshot_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / SNAPSHOT_RELPATH


def write_snapshot(db: Any, *, path: Optional[Path] = None, days: int = WINDOW_DAYS) -> Dict[str, Any]:
    """Compute the counters from *db* and write the snapshot; returns it."""
    from utils import atomic_json_write

    counters = db.agent_usage_counters(days=days)
    if not isinstance(counters, dict):
        raise TypeError("agent_usage_counters returned no mapping")
    snapshot = {"version": SNAPSHOT_VERSION, "generated_at": int(time.time()), **counters}
    atomic_json_write(path or snapshot_path(), snapshot)
    return snapshot


def maybe_refresh_snapshot(agent: Any, *, now: Optional[float] = None) -> bool:
    """Refresh the snapshot from the agent's session DB when it is older than
    ``SNAPSHOT_MIN_INTERVAL_S``. Main chats only; never raises."""
    try:
        if getattr(agent, "_delegate_depth", 0) or getattr(agent, "_is_background_review_fork", False):
            return False
        db = getattr(agent, "_session_db", None)
        if db is None or not hasattr(db, "agent_usage_counters"):
            return False
        path = snapshot_path()
        current = time.time() if now is None else now
        try:
            if current - path.stat().st_mtime < SNAPSHOT_MIN_INTERVAL_S:
                return False
        except OSError:
            pass
        write_snapshot(db, path=path)
        return True
    except Exception as exc:
        logger.debug("agent usage snapshot skipped: %s", exc)
        return False


def read_snapshot(path: Optional[Path] = None) -> Dict[str, Any]:
    """The last snapshot, or ``{}`` when there is none or it is unreadable."""
    try:
        data = json.loads((path or snapshot_path()).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


__all__ = ["SNAPSHOT_RELPATH", "maybe_refresh_snapshot", "read_snapshot", "snapshot_path", "write_snapshot"]
