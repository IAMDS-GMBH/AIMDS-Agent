"""``workdays`` — working days, DACH public holidays and target hours as data.

Session 20260829_205458_1fecc5: the model typed every calendar fact into
SQL — weekdays per month counted in a comment, Easter-derived holidays from
memory (a week off), the federal state guessed (BW, then Bayern), January
deducted twice. Five recomputations, each "verified". This tool is the
rung on the data-handling ladder that makes such facts a query:

* ``holidays`` / ``workdays`` / ``target_hours`` — computed from
  ``tools.workday_calendar`` for a date range and region,
* ``materialize`` — the same, written to the ``workday_calendar`` table in
  ``~/.hermes/state.db`` so the ``sql`` tool joins target hours against the
  auto-ingested worklogs in ``mcp_records``,
* ``configure`` — the user's work-time profile (region, week model, half
  days) saved to memory (memory MCP, Obsidian vault as fallback) as the note
  ``Arbeitszeit-Profil``; every later call reads it back.

Without a profile and without explicit parameters the tool does not guess
a region — it answers ``worktime profile unknown`` with clarify choices.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import workday_calendar as wc
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

PROFILE_TITLE = "Arbeitszeit-Profil"
PROFILE_KEYS = ("region", "weekly_hours", "days_per_week", "half_days", "employment_start", "employment_end", "part_time_factor", "notes")
TABLE = "workday_calendar"
_PROFILE_TTL_SECONDS = 600

CLARIFY_CHOICES = [
    "Bayern (DE-BY)",
    "Baden-Württemberg (DE-BW)",
    "another German state (DE-…)",
    "Austria (AT, or AT-W, AT-NÖ, …)",
    "Switzerland (CH-ZH, CH-BE, …)",
]

_profile_cache: Dict[str, Any] = {"at": 0.0, "profile": None, "source": ""}


# ---------------------------------------------------------------------------
# Profile (memory first, legacy config second, never a silent default)
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def _facade():
    from agent.memory_facade import MemoryFacade

    return MemoryFacade.for_process()


def _parse_profile_text(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        key, sep, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_").replace("-", "_")
        if not sep or key not in PROFILE_KEYS:
            continue
        value = value.strip()
        if key == "half_days":
            out[key] = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
        elif key in ("weekly_hours", "part_time_factor"):
            try:
                out[key] = float(value.replace(",", "."))
            except ValueError:
                continue
        elif key == "days_per_week":
            try:
                out[key] = int(value)
            except ValueError:
                continue
        elif value:
            out[key] = value
    return out


def _profile_from_memory() -> Optional[Dict[str, Any]]:
    try:
        facade = _facade()
        if facade.mode == "none":
            return None
        for hit in facade.search(PROFILE_TITLE, limit=3):
            title = str(hit.get("title") or "").strip().lower()
            if title != PROFILE_TITLE.lower():
                continue
            content = str(hit.get("content") or hit.get("preview") or "")
            if not content and hit.get("slug"):
                content = str(facade.read(str(hit["slug"])) or "")
            parsed = _parse_profile_text(content)
            if parsed:
                parsed["_source"] = f"memory ({facade.mode})"
                return parsed
    except Exception as exc:
        logger.debug("workdays: profile lookup failed: %s", exc)
    return None


def _profile_from_legacy_config() -> Optional[Dict[str, Any]]:
    """``state:`` / ``bundesland:`` in config.yaml (older installs)."""
    try:
        import yaml
        from hermes_constants import get_config_path

        path = get_config_path()
        if not path.exists():
            return None
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        code = str(cfg.get("state") or cfg.get("bundesland") or "").strip()
        if not code:
            return None
        return {"region": wc.normalize_region(code), "_source": "config (legacy state key)"}
    except Exception:
        return None


def load_profile(force: bool = False) -> Optional[Dict[str, Any]]:
    now = time.time()
    if not force and _profile_cache["profile"] is not None and now - _profile_cache["at"] < _PROFILE_TTL_SECONDS:
        return dict(_profile_cache["profile"])
    profile = _profile_from_memory() or _profile_from_legacy_config()
    _profile_cache.update({"at": now, "profile": profile})
    return dict(profile) if profile else None


def _profile_text(profile: Dict[str, Any]) -> str:
    lines = [
        f"region: {profile.get('region', '')}",
        f"weekly_hours: {profile.get('weekly_hours', wc.DEFAULT_WEEKLY_HOURS):g}",
        f"days_per_week: {profile.get('days_per_week', wc.DEFAULT_DAYS_PER_WEEK)}",
        "half_days: " + ", ".join(profile.get("half_days") or []),
    ]
    for key in ("employment_start", "employment_end", "part_time_factor", "notes"):
        if profile.get(key) not in (None, ""):
            lines.append(f"{key}: {profile[key]}")
    lines.append("")
    lines.append(
        "Personal work-time profile for target-hours / overtime calculations (`workdays` tool). "
        "Half days count as 0.5 working day. Change it with `workdays(action='configure', …)`."
    )
    return "\n".join(lines)


def save_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    facade = _facade()
    if facade.mode == "none":
        _profile_cache.update({"at": time.time(), "profile": dict(profile, _source="parameters (no memory backend)")})
        return {"saved": False, "backend": "none", "note": "no memory backend available — profile applies to this call only"}
    # type "reference", not "profile": the memory server's init skill keeps
    # exactly one profile note per user and merges extras — that would eat
    # this structured note. Lookup is by title, so the type is free.
    result = facade.save(
        title=PROFILE_TITLE,
        content=_profile_text(profile),
        type="reference",
        tags=["worktime", "arbeitszeit", "sollzeit"],
        priority=8,
    )
    if result.ok:
        _profile_cache.update({"at": time.time(), "profile": dict(profile, _source=f"memory ({result.backend})")})
    return {"saved": bool(result.ok), "backend": result.backend, "ref": getattr(result, "ref", "") or "", "error": getattr(result, "error", None)}


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------


def _unknown_profile(missing: List[str]) -> str:
    return json.dumps({
        "error": "worktime profile unknown",
        "missing": missing,
        "ask": (
            "Work-time / target-hours questions need the user's country and state/canton plus the week model "
            "(hours per week, days per week, half days). Ask the user with `clarify` using the choices below "
            "(in the user's language), then persist the answer with workdays(action='configure', region=…, "
            "weekly_hours=…, days_per_week=…, half_days=[…]) — it is saved to memory. Never assume."
        ),
        "clarify_choices": CLARIFY_CHOICES,
        "week_model_defaults": {"weekly_hours": wc.DEFAULT_WEEKLY_HOURS, "days_per_week": wc.DEFAULT_DAYS_PER_WEEK, "half_days": list(wc.DEFAULT_HALF_DAYS)},
        "valid_regions": wc.valid_regions(),
        "then": "workdays(action='configure', region='DE-BY', weekly_hours=40, days_per_week=5, half_days=['12-24','12-31'])",
    }, ensure_ascii=False)


def _resolve(args: Dict[str, Any]) -> Dict[str, Any]:
    """Explicit parameters win; the profile fills the rest; nothing is guessed."""
    profile = load_profile() or {}
    picked: Dict[str, Any] = {"_source": {}}

    def take(key: str, default: Any = None, required: bool = False):
        if args.get(key) not in (None, "", []):
            picked[key] = args[key]
            picked["_source"][key] = "parameter"
        elif profile.get(key) not in (None, "", []):
            picked[key] = profile[key]
            picked["_source"][key] = profile.get("_source", "profile")
        elif default is not None and not required:
            picked[key] = default
            picked["_source"][key] = "default"
        elif required:
            picked.setdefault("_missing", []).append(key)

    take("region", required=True)
    # The week model is only defaulted once a profile exists (or parameters
    # say so); without any profile the model must ask, not assume 40h/5 days.
    has_profile = bool(profile)
    take("weekly_hours", wc.DEFAULT_WEEKLY_HOURS if has_profile else None, required=not has_profile and args.get("weekly_hours") is None)
    take("days_per_week", wc.DEFAULT_DAYS_PER_WEEK if has_profile else None, required=not has_profile and args.get("days_per_week") is None)
    take("half_days", list(wc.DEFAULT_HALF_DAYS) if has_profile else list(wc.DEFAULT_HALF_DAYS))
    take("part_time_factor", 1.0)
    take("employment_start")
    take("employment_end")
    return picked


def _range(args: Dict[str, Any]) -> tuple:
    year = args.get("year")
    if args.get("start") and args.get("end"):
        return wc.parse_iso_date(args["start"], "start"), wc.parse_iso_date(args["end"], "end")
    if year:
        y = int(year)
        return date(y, 1, 1), date(y, 12, 31)
    if args.get("start"):
        start = wc.parse_iso_date(args["start"], "start")
        return start, date(start.year, 12, 31)
    today = date.today()
    return date(today.year, 1, 1), date(today.year, 12, 31)


def _assumptions(p: Dict[str, Any], hours: float) -> Dict[str, Any]:
    return {
        "region": p["region"],
        "region_name": wc.region_name(p["region"]),
        "weekly_hours": float(p["weekly_hours"]),
        "days_per_week": int(p["days_per_week"]),
        "hours_per_day": hours,
        "part_time_factor": float(p.get("part_time_factor") or 1.0),
        "half_days": list(p.get("half_days") or []),
        "employment_start": p.get("employment_start") or None,
        "employment_end": p.get("employment_end") or None,
        "weekend": "Sa+So" if int(p["days_per_week"]) == 5 else ("So" if int(p["days_per_week"]) == 6 else "none"),
        "holiday_on_weekend": "listed, not deducted",
        "partial_holidays": "listed, not deducted (add via extra_holidays if they apply to you)",
        "source": wc.SOURCE,
        "profile_source": p.get("_source", {}),
    }


FORMULA = (
    "target_net = workdays_net × hours_per_day − vacation_deduction (from the bookings, via sql); "
    "actual = SUM(duration_seconds)/3600 of all worklogs except vacation (weekend bookings count in actual, not in target); "
    "balance = actual − target_net"
)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _act_holidays(args: Dict[str, Any]) -> str:
    p = _resolve(args)
    if "region" in p.get("_missing", []):
        return _unknown_profile(["region"])
    start, end = _range(args)
    region = wc.normalize_region(p["region"])
    weekend = wc.weekend_days(int(p.get("days_per_week") or wc.DEFAULT_DAYS_PER_WEEK))
    items = []
    for h in wc.holidays_between(start, end, region):
        row = h.as_dict()
        row["on_workday"] = h.date.isoweekday() not in weekend and h.kind != wc.KIND_PARTIAL
        items.append(row)
    return json.dumps({
        "action": "holidays", "region": region, "region_name": wc.region_name(region),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "count_on_workdays": sum(1 for i in items if i["on_workday"]),
        "holidays": items, "source": wc.SOURCE,
        "note": "kind=partial applies only in parts of the region and is never deducted; regional = state patron day (AT). Answer the user in their language.",
    }, ensure_ascii=False)


def _compute(args: Dict[str, Any]):
    p = _resolve(args)
    missing = p.get("_missing", [])
    if missing:
        return None, _unknown_profile(missing)
    start, end = _range(args)
    region = wc.normalize_region(p["region"])
    p["region"] = region
    emp_start = wc.parse_iso_date(p["employment_start"], "employment_start") if p.get("employment_start") else None
    emp_end = wc.parse_iso_date(p["employment_end"], "employment_end") if p.get("employment_end") else None
    days = wc.calendar_days(
        start, end, region,
        days_per_week=int(p["days_per_week"]),
        half_days=p.get("half_days") or None,
        extra_holidays=args.get("extra_holidays") or None,
        employment_start=emp_start, employment_end=emp_end,
    )
    hours = wc.hours_per_day(float(p["weekly_hours"]), int(p["days_per_week"]), float(p.get("part_time_factor") or 1.0))
    months = wc.monthly_summary(days, hours)
    return (p, start, end, days, hours, months), None


def _act_workdays(args: Dict[str, Any], with_hours: bool, all_days: bool = False) -> str:
    computed, err = _compute(args)
    if err:
        return err
    p, start, end, days, hours, months = computed
    payload: Dict[str, Any] = {
        "action": "days" if all_days else ("target_hours" if with_hours else "workdays"),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "assumptions": _assumptions(p, hours),
        "months": months,
        "totals": wc.totals(months),
    }
    if not with_hours:
        for m in payload["months"]:
            m.pop("target_hours", None)
        payload["totals"].pop("target_hours", None)
    else:
        payload["formula"] = FORMULA
    if all_days or args.get("include_days"):
        payload["days"] = [
            {"day": d.day.isoformat(), "weekday": d.day.isoweekday(), "factor": d.factor, "reason": d.reason,
             **({"holiday": d.holiday.name} if d.holiday else {})}
            for d in days
        ]
    payload["next"] = (
        "For actual-vs-target comparisons: workdays(action='materialize', …), then JOIN workday_calendar "
        "against mcp_records with sql. Present results in the user's language."
    )
    return json.dumps(payload, ensure_ascii=False)


def _act_materialize(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    if not (args.get("start") or args.get("year")):
        today = date.today()
        args = dict(args, start=date(today.year - 1, 1, 1).isoformat(), end=date(today.year + 1, 12, 31).isoformat())
    computed, err = _compute(args)
    if err:
        return err
    p, start, end, days, hours, months = computed
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        (
            d.day.isoformat(), d.day.strftime("%Y-%m"), d.day.isocalendar()[1], d.day.isoweekday(),
            int(d.is_weekend), int(d.holiday is not None), d.holiday.name if d.holiday else None,
            d.holiday.kind if d.holiday else None, d.factor, round(d.factor * hours, 2), d.reason,
            p["region"], int(p["days_per_week"]), float(p["weekly_hours"]), generated,
        )
        for d in days
    ]
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        with conn:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                    day TEXT PRIMARY KEY, month TEXT, iso_week INTEGER, weekday INTEGER,
                    is_weekend INTEGER, is_holiday INTEGER, holiday_name TEXT, holiday_kind TEXT,
                    factor REAL, target_hours REAL, reason TEXT, region TEXT, days_per_week INTEGER,
                    weekly_hours REAL, generated_at TEXT)"""
            )
            conn.execute(f"DELETE FROM {TABLE} WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
            conn.executemany(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    finally:
        conn.close()
    return json.dumps({
        "action": "materialize", "table": TABLE, "rows": len(rows),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "assumptions": _assumptions(p, hours),
        "totals": wc.totals(months),
        "columns": ["day", "month", "iso_week", "weekday", "is_weekend", "is_holiday", "holiday_name", "holiday_kind",
                    "factor", "target_hours", "reason", "region", "days_per_week", "weekly_hours", "generated_at"],
        "example_sql": (
            "WITH ist AS (SELECT substr(timestamp, 1, 10) AS day, SUM(duration_seconds) / 3600.0 AS hours "
            "FROM mcp_records WHERE tool_name = 'mcp_TempoMCP_retrieveWorklogs' AND reference_key != 'IAMDS-595' GROUP BY 1) "
            f"SELECT c.month, ROUND(SUM(c.target_hours), 2) AS soll_brutto, ROUND(COALESCE(SUM(ist.hours), 0), 2) AS ist "
            f"FROM {TABLE} c LEFT JOIN ist ON ist.day = c.day "
            f"WHERE c.day BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' GROUP BY c.month ORDER BY c.month"
        ),
        "formula": FORMULA,
        "note": (
            "One calendar row per day: aggregate worklogs per day (CTE) BEFORE joining, or target hours multiply. "
            "Vacation deduction (e.g. central ticket IAMDS-595: 0.5h booked = half day, 1h = full day) comes from "
            "the bookings via sql; weekend worklogs count in actual, not in target. Present results in the user's language."
        ),
    }, ensure_ascii=False)


def _act_configure(args: Dict[str, Any]) -> str:
    if not args.get("region"):
        return tool_error("configure needs region (e.g. DE-BY, AT-W, CH-ZH) — ask the user first, never assume", success=False)
    profile: Dict[str, Any] = {"region": wc.normalize_region(args["region"])}
    profile["weekly_hours"] = float(args.get("weekly_hours") or wc.DEFAULT_WEEKLY_HOURS)
    profile["days_per_week"] = int(args.get("days_per_week") or wc.DEFAULT_DAYS_PER_WEEK)
    wc.hours_per_day(profile["weekly_hours"], profile["days_per_week"])  # validates
    half = args.get("half_days")
    profile["half_days"] = list(half) if isinstance(half, list) else list(wc.DEFAULT_HALF_DAYS) if half is None else [s.strip() for s in str(half).split(",") if s.strip()]
    wc._half_day_set(profile["half_days"], [date.today().year])  # validates format
    for key in ("employment_start", "employment_end"):
        if args.get(key):
            profile[key] = wc.parse_iso_date(args[key], key).isoformat()
    if args.get("part_time_factor"):
        profile["part_time_factor"] = float(args["part_time_factor"])
    if args.get("notes"):
        profile["notes"] = str(args["notes"]).strip()
    result = save_profile(profile)
    return json.dumps({"action": "configure", "profile": profile, "memory": result, "title": PROFILE_TITLE}, ensure_ascii=False)


def _act_profile() -> str:
    profile = load_profile(force=True)
    if not profile:
        return _unknown_profile(["region", "weekly_hours", "days_per_week"])
    source = profile.pop("_source", "")
    return json.dumps({"action": "profile", "profile": profile, "source": source, "title": PROFILE_TITLE}, ensure_ascii=False)


ACTIONS = ("holidays", "workdays", "target_hours", "days", "materialize", "configure", "profile")


def execute_workdays(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    action = str(args.get("action") or "target_hours").strip().lower()
    try:
        if action == "holidays":
            return _act_holidays(args)
        if action == "workdays":
            return _act_workdays(args, with_hours=False)
        if action == "target_hours":
            return _act_workdays(args, with_hours=True)
        if action == "days":
            return _act_workdays(args, with_hours=True, all_days=True)
        if action == "materialize":
            return _act_materialize(args, db_path=db_path)
        if action == "configure":
            return _act_configure(args)
        if action == "profile":
            return _act_profile()
        return tool_error(f"unknown action '{action}'; one of {', '.join(ACTIONS)}", success=False)
    except ValueError as exc:
        return tool_error(str(exc), success=False)
    except sqlite3.Error as exc:
        return tool_error(f"SQLite error: {exc}", success=False)


WORKDAYS_SCHEMA = {
    "name": "workdays",
    "description": (
        "Deterministic calendar facts for work-time questions: working days, public holidays for DE/AT/CH "
        "(per state/canton), target hours, half days (24./31.12.), 5- or 6-day weeks.\n"
        "MANDATORY for target hours, overtime, working days, public holidays, bridge days: NEVER type calendars, weekday "
        "counts or holiday dates into SQL or prose, never compute Easter yourself.\n"
        "Actions: 'target_hours' (per-month working days + target hours, default), 'days' (the same plus EVERY "
        "calendar day of the range in one call: weekday, factor 1/0.5/0, reason, holiday name — ask once for the "
        "whole range, never month by month), 'workdays', 'holidays', 'materialize' (writes table workday_calendar "
        "into ~/.hermes/state.db so `sql` can JOIN target hours against the ingested worklogs in mcp_records — use "
        "this for actual-vs-target comparisons), 'profile' (show the saved work-time profile), 'configure' (save "
        "region/week model to memory as 'Arbeitszeit-Profil').\n"
        "If the answer is 'worktime profile unknown': ask the user with `clarify` using the returned choices (in the "
        "user's language), then call action='configure' — never assume a state or week model. Present results in "
        "the user's language."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS), "description": "What to compute (default target_hours)."},
            "start": {"type": "string", "description": "Range start, YYYY-MM-DD (inclusive)."},
            "end": {"type": "string", "description": "Range end, YYYY-MM-DD (inclusive)."},
            "year": {"type": "integer", "description": "Whole year instead of start/end."},
            "region": {"type": "string", "description": "DE-BY, DE-BW, …, AT, AT-W, …, CH-ZH, … (or a name like 'Bayern'). Taken from the saved profile when omitted."},
            "weekly_hours": {"type": "number", "description": "Contract hours per week (profile default)."},
            "days_per_week": {"type": "integer", "enum": [5, 6, 7], "description": "Working days per week (profile default)."},
            "part_time_factor": {"type": "number", "description": "0 < factor <= 1 (default 1)."},
            "half_days": {"type": "array", "items": {"type": "string"}, "description": "MM-DD or YYYY-MM-DD days counted as half a working day (profile default: 12-24, 12-31)."},
            "extra_holidays": {"type": "array", "items": {"type": "string"}, "description": "Additional company holidays, YYYY-MM-DD."},
            "employment_start": {"type": "string", "description": "YYYY-MM-DD; days before it carry no target time."},
            "employment_end": {"type": "string", "description": "YYYY-MM-DD; days after it carry no target time."},
            "include_days": {"type": "boolean", "description": "Also return one entry per calendar day."},
            "notes": {"type": "string", "description": "configure only: free-text note stored with the profile."},
        },
    },
}


def _handle_workdays(args: dict, **kw) -> str:
    return execute_workdays(dict(args or {}))


registry.register(
    name="workdays",
    toolset="workdays",
    schema=WORKDAYS_SCHEMA,
    handler=_handle_workdays,
    check_fn=lambda: True,
    emoji="📅",
)
