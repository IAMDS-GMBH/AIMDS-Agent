"""Work feed: the user's tickets, mail, calendar and tasks in one list (AIS-797).

The desktop's start page and task list read it from ``GET
/api/workspace/feed``; the CLI shows it with ``/today``. It is the brief
collector run for the ``workspace`` window: only active sources
(``SourceAdapter.availability``) are read, with the collector's per-call
and per-source time budgets, LLM-free. Items keep their status history in
the brief store (``prev_status`` / ``status_changed_at``). The shape is the
``workspace_feed`` contract (agent/contracts).

The result is cached for ``cron.brief_collector.feed_ttl_seconds``; one
collection runs at a time and concurrent callers get its result. Callers on
the event loop run :func:`get_feed` in a worker thread.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from cron.brief_collector import DEFAULT_CFG, FEED_KIND, collect, collector_config
from cron.brief_sources.base import BriefItem, SourceStatus

logger = logging.getLogger(__name__)

FEED_VERSION = 1
FEED_JOB_ID = "workspace-feed"

_lock = threading.Lock()
_cache: Dict[str, Any] = {}


def item_dict(item: BriefItem) -> Dict[str, Any]:
    """A ``FeedItem``: empty optional fields left out."""
    out: Dict[str, Any] = {
        "id": item.id, "source": item.source, "kind": item.kind, "key": item.key,
        "title": item.title, "status": item.status, "mine": bool(item.mine),
        "prev_status": item.prev_status, "status_changed_at": item.status_changed_at,
        "first_seen_at": item.first_seen_at,
    }
    for name in ("priority", "due_at", "starts_at", "ends_at", "updated_at", "url"):
        value = getattr(item, name)
        if value:
            out[name] = value
    if item.extra:
        out["extra"] = dict(item.extra)
    return out


def source_dict(status: SourceStatus) -> Dict[str, Any]:
    return {
        "name": status.name, "label": status.label, "status": status.status, "detail": status.detail,
        "count": int(status.count), "partial": bool(status.partial), "unavailable": status.unavailable,
    }


def build_feed(**collect_kwargs: Any) -> Dict[str, Any]:
    """Collect the ``workspace`` window now (no cache); kwargs go to ``collect``."""
    result = collect({"id": FEED_JOB_ID}, FEED_KIND, **collect_kwargs)
    window = result.window
    return {
        "v": FEED_VERSION,
        "generated_at": window.now.isoformat(timespec="seconds"),
        "window": {
            "kind": FEED_KIND, "today": window.today.isoformat(), "preview_day": window.preview_day.isoformat(),
            "start": window.start.isoformat(timespec="seconds"), "end": window.end.isoformat(timespec="seconds"),
        },
        "items": [item_dict(i) for i in result.items],
        "sources": [source_dict(s) for s in result.sources],
    }


def feed_ttl(cfg: Optional[Dict[str, Any]]) -> float:
    try:
        return max(0.0, float(collector_config(cfg).get("feed_ttl_seconds")))
    except (TypeError, ValueError):
        return float(DEFAULT_CFG["feed_ttl_seconds"])


def get_feed(*, refresh: bool = False, cfg: Optional[Dict[str, Any]] = None, **collect_kwargs: Any) -> Dict[str, Any]:
    """The cached feed, collected again when older than the TTL or on *refresh*."""
    if cfg is None:
        try:
            from hermes_cli.config import load_config

            cfg = load_config()
        except Exception as exc:
            logger.debug("config unavailable for the work feed: %s", exc)
            cfg = {}
    ttl = feed_ttl(cfg)
    with _lock:
        feed, at = _cache.get("feed"), float(_cache.get("at") or 0.0)
        age = time.monotonic() - at
        if feed is not None and not refresh and age < ttl:
            return {**feed, "cached": True, "age_s": round(age, 1)}
        t0 = time.monotonic()
        feed = build_feed(cfg=cfg, **collect_kwargs)
        _cache.update(feed=feed, at=time.monotonic())
        logger.info("work feed collected: %d items in %.1fs (%s)", len(feed["items"]), time.monotonic() - t0,
                    " | ".join(f"{s['name']}={s['status']}" for s in feed["sources"]))
        return {**feed, "cached": False, "age_s": 0.0}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def format_feed(feed: Dict[str, Any]) -> list:
    """Plain-text lines for the CLI ``/today`` probe."""
    lines = [f"Work feed {feed['window']['today']} (generated {feed['generated_at']}"
             + (f", cached {feed.get('age_s', 0):.0f}s" if feed.get("cached") else "") + ")"]
    lines.append("Sources: " + " | ".join(
        f"{s['name']}={s['status']}" + (f"({s['detail']})" if s.get("detail") else "") for s in feed["sources"]
    ))
    by_kind: Dict[str, list] = {}
    for item in feed["items"]:
        by_kind.setdefault(item["kind"], []).append(item)
    for kind in ("event", "ticket", "task", "mail", "chat", "worklog", "note"):
        rows = by_kind.get(kind) or []
        if not rows:
            continue
        lines.append(f"{kind} ({len(rows)})")
        for item in rows:
            when = item.get("starts_at") or item.get("due_at") or item.get("updated_at") or ""
            change = f" [{item['prev_status']} → {item['status']}]" if item.get("prev_status") else (
                f" [{item['status']}]" if item.get("status") else "")
            key = f"{item['key']} " if kind in ("ticket", "worklog") else ""
            lines.append(f"  {when[:16]:<16} {item['source']:<11} {key}{item['title']}{change}")
    return lines
