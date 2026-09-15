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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import workday_calendar as wc
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

PROFILE_TITLE = "Arbeitszeit-Profil"
PROFILE_KEYS = (
    "region", "weekly_hours", "days_per_week", "work_weekdays", "employment_label", "half_days",
    "employment_start", "employment_end", "part_time_factor",
    "municipality", "plz", "partial_holidays",
    "worklog_source_tool", "vacation_booking_patterns", "vacation_hour_factor",
    "presence_calendar", "presence_match_patterns", "presence_default", "notes",
)
TABLE = "workday_calendar"
ABSENCES_TABLE = "absences"
PRESENCE_TABLE = "presence"
PERIODS = ("ytd", "mtd", "this_month", "last_month", "this_week", "last_week", "through_last_week")
PRESENCE_KINDS = ("office", "homeoffice", "travel")
DEFAULT_PRESENCE = "homeoffice"  # what a booked working day counts as when nothing else is recorded
_PROFILE_TTL_SECONDS = 600
# A missing profile is re-checked soon: a lookup that failed for a transient
# reason (backend down, envelope bug) must not hide a working save for 10 min.
_PROFILE_NEGATIVE_TTL_SECONDS = 30

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


def _today() -> date:
    """Today in the configured HERMES_TIMEZONE, not server-local time.

    The prompt's calendar block uses hermes_time.now(); date.today() here
    could disagree with it around midnight in a non-local timezone (AIS-275).
    """
    try:
        from hermes_time import now as _hermes_now

        return _hermes_now().date()
    except Exception:
        return date.today()


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
        if key in ("half_days", "work_weekdays", "partial_holidays"):
            items = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
            # partial_holidays distinguishes "unset" (never clarified) from
            # "[]" (user confirmed none apply); the flat text stores the
            # latter as the sentinel `none` (AIS-277).
            if key == "partial_holidays" and items and all(v.lower() in ("none", "keine") for v in items):
                items = []
            out[key] = items
        elif key in ("weekly_hours", "part_time_factor", "vacation_hour_factor"):
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


def _normalize_title(value: Any) -> str:
    """Title as the memory server may hand it back: lowercase, trimmed, and
    without an upsert marker such as ``Arbeitszeit-Profil (updated)``."""
    text = " ".join(str(value or "").split()).strip().lower()
    for suffix in (" (updated)", " (aktualisiert)"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _profile_slug() -> str:
    from agent.memory_facade import _slugify

    return _slugify(PROFILE_TITLE)


def _is_profile_hit(hit: Dict[str, Any]) -> bool:
    if _normalize_title(hit.get("title")) == PROFILE_TITLE.lower():
        return True
    slug = str(hit.get("slug") or "").strip().lower()
    return bool(slug) and slug == _profile_slug()


def _profile_text_from_hit(hit: Dict[str, Any], facade: Any) -> str:
    """The profile note's body for a search hit.

    Session 20260915_082908 (AIS-337/AIS-344): every ``configure`` since
    2026-08-31 answered ``saved: true`` and every later session still got
    ``worktime profile unknown``. Root cause was the MCP bridge envelope
    (``{"result": …}``) that ``MemoryFacade`` did not unwrap — fixed there.
    This helper stays defensive: search hits may carry only a truncated
    ``snippet``; read the note by slug and accept either the plain body or a
    JSON object with ``content``.
    """
    text = str(hit.get("content") or hit.get("preview") or hit.get("snippet") or "")
    if hit.get("content"):
        return text
    # preview/snippet are truncated — a 40-char snippet still "parses" (region
    # + weekly_hours) but drops the worklog/presence patterns; read the note.
    slug = str(hit.get("slug") or "").strip()
    if not slug:
        return text
    try:
        raw = facade.read(slug)
    except Exception as exc:
        logger.debug("workdays: profile read failed: %s", exc)
        return text
    if not raw:
        return text
    raw = str(raw)
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                inner = payload.get("content") or payload.get("body") or ""
                if isinstance(inner, str) and inner.strip():
                    return inner
        except Exception:
            pass
    return raw


def _profile_from_memory() -> Optional[Dict[str, Any]]:
    try:
        facade = _facade()
        if facade.mode == "none":
            return None
        for hit in facade.search(PROFILE_TITLE, limit=5):
            if not isinstance(hit, dict) or not _is_profile_hit(hit):
                continue
            parsed = _parse_profile_text(_profile_text_from_hit(hit, facade))
            if parsed:
                parsed["_source"] = f"memory ({facade.mode})"
                return parsed
    except Exception as exc:
        logger.debug("workdays: profile lookup failed: %s", exc)
    return None


_MIRROR_KEY = "worktime_profile"


def _mirror_db_path() -> Path:
    return _default_db_path()


def _profile_from_mirror(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The local copy in ``state.db`` (``state_meta.worktime_profile``).

    Memory stays the source of truth; the mirror is what cron runs and
    sessions without a memory backend read (AIS-337). Never config.yaml.
    """
    path = db_path or _mirror_db_path()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS state_meta (key TEXT PRIMARY KEY, value TEXT)")
            row = conn.execute("SELECT value FROM state_meta WHERE key = ?", (_MIRROR_KEY,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("workdays: profile mirror read failed: %s", exc)
        return None
    if not row or not row[0]:
        return None
    try:
        data = json.loads(row[0])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    profile = {k: v for k, v in data.items() if k in PROFILE_KEYS and v not in (None, "")}
    if not profile.get("region"):
        return None
    profile["_source"] = "state.db mirror"
    return profile


def _save_profile_mirror(profile: Dict[str, Any], db_path: Optional[Path] = None) -> bool:
    path = db_path or _mirror_db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10.0)
        try:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS state_meta (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute(
                    "INSERT INTO state_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_MIRROR_KEY, json.dumps({k: v for k, v in profile.items() if k in PROFILE_KEYS}, ensure_ascii=False)),
                )
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError) as exc:
        logger.debug("workdays: profile mirror write failed: %s", exc)
        return False


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
    cached = _profile_cache["profile"]
    age = now - float(_profile_cache.get("at") or 0.0)
    if not force and cached is not None and age < _PROFILE_TTL_SECONDS:
        return dict(cached)
    if not force and cached is None and _profile_cache.get("checked") and age < _PROFILE_NEGATIVE_TTL_SECONDS:
        return None
    profile = _profile_from_memory() or _profile_from_mirror() or _profile_from_legacy_config()
    _profile_cache.update({"at": now, "profile": profile, "checked": True})
    return dict(profile) if profile else None


def _profile_text(profile: Dict[str, Any]) -> str:
    lines = [
        f"region: {profile.get('region', '')}",
        f"weekly_hours: {profile.get('weekly_hours', wc.DEFAULT_WEEKLY_HOURS):g}",
        f"days_per_week: {profile.get('days_per_week', wc.DEFAULT_DAYS_PER_WEEK)}",
    ]
    if profile.get("work_weekdays"):
        lines.append("work_weekdays: " + ", ".join(str(v) for v in profile["work_weekdays"]))
    lines.append("half_days: " + ", ".join(profile.get("half_days") or []))
    if isinstance(profile.get("partial_holidays"), list):
        lines.append("partial_holidays: " + (", ".join(profile["partial_holidays"]) or "none"))
    for key in ("employment_start", "employment_end", "part_time_factor", "employment_label",
                "municipality", "plz",
                "worklog_source_tool", "vacation_booking_patterns", "vacation_hour_factor",
                "presence_calendar", "presence_match_patterns", "presence_default", "notes"):
        if profile.get(key) not in (None, ""):
            lines.append(f"{key}: {profile[key]}")
    lines.append("")
    lines.append(
        "Personal work-time profile for target-hours / overtime calculations (`workdays` tool). "
        "Half days count as 0.5 working day. Change it with `workdays(action='configure', …)`."
    )
    return "\n".join(lines)


def save_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    mirrored = _save_profile_mirror(profile)
    facade = _facade()
    if facade.mode == "none":
        source = "state.db mirror" if mirrored else "parameters (no memory backend)"
        _profile_cache.update({"at": time.time(), "profile": dict(profile, _source=source)})
        return {
            "saved": mirrored, "backend": "state.db" if mirrored else "none",
            "mirror": "state.db" if mirrored else None,
            "note": (
                "no memory backend available — profile kept in the local state.db mirror only"
                if mirrored else "no memory backend available — profile applies to this call only"
            ),
        }
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
    elif mirrored:
        _profile_cache.update({"at": time.time(), "profile": dict(profile, _source="state.db mirror")})
    return {
        "saved": bool(result.ok), "backend": result.backend, "ref": getattr(result, "ref", "") or "",
        "error": getattr(result, "error", None), "mirror": "state.db" if mirrored else None,
    }


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------


def _unknown_profile(missing: List[str]) -> str:
    return json.dumps({
        "error": "worktime profile unknown",
        "missing": missing,
        "ask": (
            "Ask the user (in their language) for state/canton and the week model — hours/week, working days "
            "(Mo-Fr, Mo-Sa, or work_weekdays like Mo-We), half days — then workdays(action='configure', …). Never assume."
        ),
        "clarify_choices": CLARIFY_CHOICES,
        "estimate": "action='estimate_profile' proposes a week model from ingested worklogs — user must CONFIRM before configure.",
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
    take("work_weekdays")
    take("employment_label")
    take("municipality")
    take("plz")
    # partial_holidays needs its own resolution: `take()` treats [] as absent,
    # but here an empty list is MEANINGFUL — the user confirmed that none of
    # the region's municipal holidays apply (unset = never clarified, AIS-277).
    if isinstance(args.get("partial_holidays"), list):
        picked["partial_holidays"] = args["partial_holidays"]
        picked["_source"]["partial_holidays"] = "parameter"
    elif isinstance(profile.get("partial_holidays"), list):
        picked["partial_holidays"] = profile["partial_holidays"]
        picked["_source"]["partial_holidays"] = profile.get("_source", "profile")
    take("worklog_source_tool")
    take("vacation_booking_patterns")
    take("vacation_hour_factor", 1.0)
    take("presence_calendar")
    take("presence_match_patterns")
    take("presence_default", DEFAULT_PRESENCE)
    weekday_set = wc.parse_work_weekdays(picked.get("work_weekdays"))
    if weekday_set:
        explicit = picked.get("days_per_week")
        if picked["_source"].get("days_per_week") == "parameter" and int(explicit) != len(weekday_set):
            raise ValueError(
                f"days_per_week={explicit} contradicts work_weekdays ({len(weekday_set)} days) — pass only one of them"
            )
        picked["days_per_week"] = len(weekday_set)
        picked["_source"]["days_per_week"] = "derived (work_weekdays)"
        if "days_per_week" in picked.get("_missing", []):
            picked["_missing"].remove("days_per_week")
    return picked


def _period_range(period: str, today: date, employment_start: Optional[date] = None) -> tuple:
    """Deterministic date ranges for relative periods (AIS-338).

    Session 20260915_082908 asked for "through the end of last week" and the
    model typed the range by hand — and slipped a week. ISO weeks start on
    Monday; every keyword is resolved from today, never guessed.
    """
    key = str(period or "").strip().lower().replace("-", "_")
    monday = today - timedelta(days=today.weekday())
    first_of_month = today.replace(day=1)
    if key == "ytd":
        start = date(today.year, 1, 1)
        if employment_start and employment_start > start:
            start = employment_start
        return start, today
    if key == "mtd":
        return first_of_month, today
    if key == "this_month":
        nxt = (first_of_month + timedelta(days=32)).replace(day=1)
        return first_of_month, nxt - timedelta(days=1)
    if key == "last_month":
        last_end = first_of_month - timedelta(days=1)
        return last_end.replace(day=1), last_end
    if key == "this_week":
        return monday, monday + timedelta(days=6)
    if key == "last_week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if key == "through_last_week":
        end = monday - timedelta(days=1)
        start = date(end.year, 1, 1)
        if employment_start and employment_start > start:
            start = employment_start
        return start, end
    raise ValueError(f"unknown period '{period}'; one of {', '.join(PERIODS)}")


def _range(args: Dict[str, Any]) -> tuple:
    year = args.get("year")
    if args.get("period"):
        emp = args.get("employment_start")
        return _period_range(
            str(args["period"]), _today(),
            wc.parse_iso_date(emp, "employment_start") if emp else None,
        )
    if args.get("start") and args.get("end"):
        return wc.parse_iso_date(args["start"], "start"), wc.parse_iso_date(args["end"], "end")
    if year:
        y = int(year)
        return date(y, 1, 1), date(y, 12, 31)
    if args.get("start"):
        start = wc.parse_iso_date(args["start"], "start")
        return start, date(start.year, 12, 31)
    today = _today()
    return date(today.year, 1, 1), date(today.year, 12, 31)


_DAY_ABBR = {1: "Mo", 2: "Tu", 3: "We", 4: "Th", 5: "Fr", 6: "Sa", 7: "Su"}


def _assumptions(p: Dict[str, Any], hours: float) -> Dict[str, Any]:
    weekday_set = wc.parse_work_weekdays(p.get("work_weekdays"))
    weekend = wc.weekend_days(int(p["days_per_week"]), weekday_set)
    out = {
        "region": p["region"],
        "region_name": wc.region_name(p["region"]),
        "weekly_hours": float(p["weekly_hours"]),
        "days_per_week": int(p["days_per_week"]),
        "hours_per_day": hours,
        "part_time_factor": float(p.get("part_time_factor") or 1.0),
        "half_days": list(p.get("half_days") or []),
        "employment_start": p.get("employment_start") or None,
        "employment_end": p.get("employment_end") or None,
        "weekend": "+".join(_DAY_ABBR[d] for d in sorted(weekend)) or "none",
        "holiday_on_weekend": "listed, not deducted",
        "source": wc.SOURCE,
        "profile_source": p.get("_source", {}),
    }
    partials = p.get("partial_holidays")
    if isinstance(partials, list):
        out["partial_holidays"] = list(partials) if partials else "none apply (user confirmed)"
    else:
        out["partial_holidays"] = (
            "unresolved — municipal/partial holidays are NOT deducted; confirm with the user and persist "
            "via workdays(action='configure', partial_holidays=[…] or [])"
        )
    if weekday_set:
        out["work_weekdays"] = [_DAY_ABBR[d] for d in sorted(weekday_set)]
    if p.get("employment_label"):
        out["employment_label"] = str(p["employment_label"])
    if p.get("municipality"):
        out["municipality"] = str(p["municipality"])
    if p.get("plz"):
        out["plz"] = str(p["plz"])
    return out


def _partial_holidays_hint(p: Dict[str, Any], start: date, end: date) -> Optional[Dict[str, Any]]:
    """Ask-once hint when the region has partial holidays in the range and the
    profile never clarified whether they apply (unset; [] means clarified)."""
    if isinstance(p.get("partial_holidays"), list):
        return None
    try:
        names = sorted({
            h.name
            for year in range(start.year, end.year + 1)
            for h in wc.partial_holidays_for(p["region"], year)
            if start <= h.date <= end
        })
    except Exception:
        return None
    if not names:
        return None
    return {
        "names": names,
        "ask": (
            "These municipal/partial holidays may or may not apply to this user — they are NOT deducted yet. "
            "Ask once (municipality/PLZ helps; e.g. Augsburg = PLZ 86150-86199), then persist via "
            "workdays(action='configure', partial_holidays=[…]) — or partial_holidays=[] if none apply."
        ),
    }


FORMULA = (
    "target_net = target_gross − vacation_credit (absences.portion × per-day target_hours, via sql); "
    "actual = SUM(duration_seconds)/3600 of all worklogs except vacation bookings (weekend bookings count in actual, not in target); "
    "delta = actual − target_net"
)


def _split_patterns(value: Any) -> List[str]:
    """Comma-separated string or list → list of SQL LIKE patterns."""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _like_sql(column: str, patterns: List[str]) -> str:
    """Parameterized OR-of-LIKEs clause; bind the patterns in order."""
    return "(" + " OR ".join(f"{column} LIKE ?" for _ in patterns) + ")"


def _like_literal(column: str, patterns: List[str]) -> str:
    """Display-only OR-of-LIKEs clause with inlined literals (for example_sql)."""
    return "(" + " OR ".join(f"{column} LIKE '{p}'" for p in patterns) + ")"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _act_holidays(args: Dict[str, Any]) -> str:
    p = _resolve(args)
    if "region" in p.get("_missing", []):
        return _unknown_profile(["region"])
    start, end = _range(args)
    region = wc.normalize_region(p["region"])
    weekend = wc.weekend_days(int(p.get("days_per_week") or wc.DEFAULT_DAYS_PER_WEEK), wc.parse_work_weekdays(p.get("work_weekdays")))
    applicable = {str(n).strip().lower() for n in (p.get("partial_holidays") or [])}
    items = []
    for h in wc.holidays_between(start, end, region):
        row = h.as_dict()
        row["on_workday"] = h.date.isoweekday() not in weekend and (
            h.kind != wc.KIND_PARTIAL or h.name.lower() in applicable
        )
        items.append(row)
    payload = {
        "action": "holidays", "region": region, "region_name": wc.region_name(region),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "count_on_workdays": sum(1 for i in items if i["on_workday"]),
        "holidays": items, "source": wc.SOURCE,
        "note": (
            "kind=partial applies only in parts of the region and deducts only when confirmed in the profile's "
            "partial_holidays; regional = state patron day (AT). Answer the user in their language."
        ),
    }
    hint = _partial_holidays_hint(p, start, end)
    if hint:
        payload["partial_holidays_unresolved"] = hint
    return json.dumps(payload, ensure_ascii=False)


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
        work_weekdays=p.get("work_weekdays") or None,
        half_days=p.get("half_days") or None,
        extra_holidays=args.get("extra_holidays") or None,
        applicable_partial_holidays=p.get("partial_holidays") or None,
        employment_start=emp_start, employment_end=emp_end,
    )
    hours = wc.hours_per_day(
        float(p["weekly_hours"]), int(p["days_per_week"]), float(p.get("part_time_factor") or 1.0),
        work_weekdays=p.get("work_weekdays") or None,
    )
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
    hint = _partial_holidays_hint(p, start, end)
    if hint:
        payload["partial_holidays_unresolved"] = hint
    payload["next"] = (
        "For actual-vs-target comparisons prefer workdays(action='report') — one call, all math in SQL. "
        "Advanced path: action='materialize', then JOIN workday_calendar against mcp_records with sql. "
        "Present results in the user's language."
    )
    return json.dumps(payload, ensure_ascii=False)


def _act_materialize(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    if not (args.get("start") or args.get("year")):
        today = _today()
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
    mat_payload: Dict[str, Any] = {
        "action": "materialize", "table": TABLE, "rows": len(rows),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "assumptions": _assumptions(p, hours),
        "totals": wc.totals(months),
        "columns": ["day", "month", "iso_week", "weekday", "is_weekend", "is_holiday", "holiday_name", "holiday_kind",
                    "factor", "target_hours", "reason", "region", "days_per_week", "weekly_hours", "generated_at"],
        "example_sql": (
            "WITH ist AS (SELECT substr(timestamp, 1, 10) AS day, SUM(duration_seconds) / 3600.0 AS hours "
            f"FROM mcp_records WHERE {_like_literal('tool_name', _split_patterns(p.get('worklog_source_tool')) or ['<your worklog tool_name>'])} GROUP BY 1) "
            f"SELECT c.month, ROUND(SUM(c.target_hours), 2) AS target_gross, ROUND(COALESCE(SUM(ist.hours), 0), 2) AS actual "
            f"FROM {TABLE} c LEFT JOIN ist ON ist.day = c.day "
            f"WHERE c.day BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' GROUP BY c.month ORDER BY c.month"
        ),
        "formula": FORMULA,
        "note": (
            "One calendar row per day: aggregate worklogs per day (CTE) BEFORE joining, or target hours multiply. "
            f"Vacation credit comes from the {ABSENCES_TABLE} table (fill it via action='absences'); weekend "
            "worklogs count in actual, not in target. Prefer action='report' — it runs this join for you. "
            "Present results in the user's language."
        ),
    }
    hint = _partial_holidays_hint(p, start, end)
    if hint:
        mat_payload["partial_holidays_unresolved"] = hint
    return json.dumps(mat_payload, ensure_ascii=False)


def _act_configure(args: Dict[str, Any]) -> str:
    # Merge semantics (AIS-277): configure UPDATES the stored profile — keys
    # not passed keep their stored value instead of resetting to defaults.
    # Every hint in this tool says "re-run configure with partial_holidays=[…]"
    # (or a single other key); that must never wipe the week model or the
    # worklog source patterns.
    base = {k: v for k, v in (load_profile() or {}).items() if k in PROFILE_KEYS}
    merged = dict(base)
    merged.update({k: v for k, v in (args or {}).items() if v is not None})
    args = merged
    if not args.get("region"):
        return tool_error("configure needs region (e.g. DE-BY, AT-W, CH-ZH) — ask the user first, never assume", success=False)
    profile: Dict[str, Any] = {"region": wc.normalize_region(args["region"])}
    profile["weekly_hours"] = float(args.get("weekly_hours") or wc.DEFAULT_WEEKLY_HOURS)
    weekday_set = wc.parse_work_weekdays(args.get("work_weekdays"))
    if weekday_set:
        if args.get("days_per_week") and int(args["days_per_week"]) != len(weekday_set):
            return tool_error(
                f"days_per_week={args['days_per_week']} contradicts work_weekdays ({len(weekday_set)} days) — pass only one of them",
                success=False,
            )
        profile["work_weekdays"] = [_DAY_ABBR[d].lower() for d in sorted(weekday_set)]
        profile["days_per_week"] = len(weekday_set)
    else:
        profile["days_per_week"] = int(args.get("days_per_week") or wc.DEFAULT_DAYS_PER_WEEK)
    wc.hours_per_day(
        profile["weekly_hours"], profile["days_per_week"],
        float(args.get("part_time_factor") or 1.0), work_weekdays=weekday_set,
    )  # validates
    if args.get("employment_label"):
        label = str(args["employment_label"]).strip().lower()
        if label not in ("vollzeit", "teilzeit"):
            return tool_error("employment_label must be 'vollzeit' or 'teilzeit'", success=False)
        profile["employment_label"] = label
    if args.get("worklog_source_tool"):
        patterns = _split_patterns(args["worklog_source_tool"])
        if not patterns:
            return tool_error("worklog_source_tool must be a non-empty LIKE pattern (or comma-separated list)", success=False)
        profile["worklog_source_tool"] = ", ".join(patterns)
    if args.get("vacation_booking_patterns"):
        profile["vacation_booking_patterns"] = ", ".join(_split_patterns(args["vacation_booking_patterns"]))
    if args.get("vacation_hour_factor"):
        factor = float(args["vacation_hour_factor"])
        if factor <= 0:
            return tool_error("vacation_hour_factor must be > 0", success=False)
        profile["vacation_hour_factor"] = factor
    if args.get("presence_calendar"):
        profile["presence_calendar"] = str(args["presence_calendar"]).strip()
    if args.get("presence_match_patterns"):
        profile["presence_match_patterns"] = ", ".join(_split_patterns(args["presence_match_patterns"]))
    if args.get("presence_default"):
        default_kind = str(args["presence_default"]).strip().lower()
        if default_kind not in PRESENCE_KINDS:
            return tool_error(f"presence_default must be one of {', '.join(PRESENCE_KINDS)}", success=False)
        profile["presence_default"] = default_kind
    half = args.get("half_days")
    profile["half_days"] = list(half) if isinstance(half, list) else list(wc.DEFAULT_HALF_DAYS) if half is None else [s.strip() for s in str(half).split(",") if s.strip()]
    wc._half_day_set(profile["half_days"], [_today().year])  # validates format
    for key in ("employment_start", "employment_end"):
        if args.get(key):
            profile[key] = wc.parse_iso_date(args[key], key).isoformat()
    if args.get("part_time_factor"):
        profile["part_time_factor"] = float(args["part_time_factor"])
    if args.get("municipality"):
        profile["municipality"] = str(args["municipality"]).strip()
    if args.get("plz"):
        plz = str(args["plz"]).strip()
        if not (plz.isdigit() and 4 <= len(plz) <= 6):
            return tool_error("plz must be a 4-6 digit postal code", success=False)
        profile["plz"] = plz
    partial_arg = args.get("partial_holidays")
    if isinstance(partial_arg, str):
        partial_arg = [s.strip() for s in partial_arg.replace(";", ",").split(",") if s.strip()]
    if partial_arg is not None:
        valid = {h.name.lower(): h.name for h in wc.partial_holidays_for(profile["region"], _today().year)}
        canonical: List[str] = []
        for name in partial_arg:
            key = str(name).strip().lower()
            if not key or key in ("none", "keine"):
                continue
            if key not in valid:
                return tool_error(
                    f"unknown partial holiday '{name}' for {profile['region']}; "
                    f"valid: {', '.join(sorted(valid.values())) or 'none in this region'}",
                    success=False,
                )
            canonical.append(valid[key])
        profile["partial_holidays"] = canonical
    if args.get("notes"):
        profile["notes"] = str(args["notes"]).strip()
    result = save_profile(profile)
    response: Dict[str, Any] = {"action": "configure", "profile": profile, "memory": result, "title": PROFILE_TITLE}
    if partial_arg is None:
        # Region has municipal/partial holidays and the caller did not decide:
        # SUGGEST (never auto-save) from municipality/PLZ evidence (AIS-277).
        suggestions = wc.suggest_partial_holidays(
            profile["region"], profile.get("municipality"), profile.get("plz"), year=_today().year,
        )
        if suggestions:
            response["partial_holiday_suggestions"] = suggestions
            response["confirm"] = (
                "Municipal/partial holidays exist in this region and are not configured yet "
                "(true = municipality/PLZ evidence says it applies, null = unknown). Ask the user to confirm, "
                "then re-run configure with partial_holidays=[…] — or partial_holidays=[] if none apply."
            )
    return json.dumps(response, ensure_ascii=False)


def _act_profile() -> str:
    profile = load_profile(force=True)
    if not profile:
        return _unknown_profile(["region", "weekly_hours", "days_per_week"])
    source = profile.pop("_source", "")
    return json.dumps({"action": "profile", "profile": profile, "source": source, "title": PROFILE_TITLE}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Absences (source-neutral vacation/sick store), estimate, report
# ---------------------------------------------------------------------------


def _open_db(db_path: Optional[Path]) -> sqlite3.Connection:
    from tools.mcp_json_ingestor import init_mcp_tables

    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    init_mcp_tables(conn)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {ABSENCES_TABLE} (
            day TEXT NOT NULL,
            portion REAL NOT NULL DEFAULT 1.0,
            kind TEXT NOT NULL DEFAULT 'vacation',
            source TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (day, kind))"""
    )
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {PRESENCE_TABLE} (
            day TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'office',
            source TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (day, kind))"""
    )
    conn.commit()
    return conn


def _upsert_absences(conn: sqlite3.Connection, rows: List[tuple]) -> None:
    conn.executemany(
        f"INSERT INTO {ABSENCES_TABLE} (day, portion, kind, source, note, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(day, kind) DO UPDATE SET portion=excluded.portion, source=excluded.source, "
        "note=excluded.note, created_at=excluded.created_at",
        rows,
    )
    conn.commit()


def _absences_summary(conn: sqlite3.Connection, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict[str, Any]]:
    where, params = "", []
    if start and end:
        where, params = "WHERE day BETWEEN ? AND ?", [start, end]
    rows = conn.execute(
        f"SELECT substr(day, 1, 4) AS year, kind, COUNT(*), ROUND(SUM(portion), 2), GROUP_CONCAT(DISTINCT source) "
        f"FROM {ABSENCES_TABLE} {where} GROUP BY 1, 2 ORDER BY 1, 2",
        params,
    ).fetchall()
    return [{"year": y, "kind": k, "days": n, "portions": p, "sources": s} for y, k, n, p, s in rows]


def _act_absences(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    op = str(args.get("op") or "list").strip().lower()
    kind = str(args.get("kind") or "vacation").strip().lower()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _open_db(db_path)
    try:
        if op == "list":
            return json.dumps({"action": "absences", "op": "list", "summary": _absences_summary(conn)}, ensure_ascii=False)

        if op == "add":
            portion = float(args.get("portion") or 1.0)
            if not 0 < portion <= 1:
                return tool_error("portion must be in (0, 1]", success=False)
            source = str(args.get("source") or "user").strip()
            note = str(args.get("note") or "").strip() or None
            rows: List[tuple] = []
            for item in args.get("days") or []:
                if isinstance(item, dict) and (item.get("from") or item.get("to")):
                    d_from = wc.parse_iso_date(item.get("from"), "days.from")
                    d_to = wc.parse_iso_date(item.get("to") or item.get("from"), "days.to")
                    p = _resolve({})
                    if p.get("_missing"):
                        return _unknown_profile(p["_missing"])  # ranges expand to working days — needs the calendar
                    for d in wc.calendar_days(
                        d_from, d_to, wc.normalize_region(p["region"]),
                        days_per_week=int(p["days_per_week"]),
                        work_weekdays=p.get("work_weekdays") or None,
                        half_days=p.get("half_days") or None,
                        applicable_partial_holidays=p.get("partial_holidays") or None,
                    ):
                        if d.factor > 0:
                            rows.append((d.day.isoformat(), min(portion, d.factor), kind, source, note, now))
                elif isinstance(item, dict):
                    day = wc.parse_iso_date(item.get("day"), "days.day")
                    item_portion = float(item.get("portion") or portion)
                    rows.append((day.isoformat(), item_portion, kind, source, note, now))
                else:
                    rows.append((wc.parse_iso_date(item, "days").isoformat(), portion, kind, source, note, now))
            if not rows:
                return tool_error("op='add' needs days: ['YYYY-MM-DD', …] and/or [{'from': …, 'to': …}]", success=False)
            _upsert_absences(conn, rows)
            return json.dumps({
                "action": "absences", "op": "add", "upserted": len(rows), "kind": kind, "source": source,
                "summary": _absences_summary(conn),
            }, ensure_ascii=False)

        if op == "remove":
            conditions, params = ["kind = ?"], [kind]
            days = [wc.parse_iso_date(d, "days").isoformat() for d in args.get("days") or []]
            if days:
                conditions.append("day IN (" + ",".join("?" * len(days)) + ")")
                params += days
            elif args.get("start") or args.get("year"):
                start, end = _range(args)
                conditions.append("day BETWEEN ? AND ?")
                params += [start.isoformat(), end.isoformat()]
            elif args.get("source"):
                pass  # source alone is a valid filter
            else:
                return tool_error("op='remove' needs days, start/end/year, or source — refusing to wipe the table", success=False)
            if args.get("source"):
                conditions.append("source LIKE ?")
                params.append(str(args["source"]))
            cur = conn.execute(f"DELETE FROM {ABSENCES_TABLE} WHERE " + " AND ".join(conditions), params)
            conn.commit()
            return json.dumps({"action": "absences", "op": "remove", "deleted": cur.rowcount,
                               "summary": _absences_summary(conn)}, ensure_ascii=False)

        if op == "import_from_bookings":
            p = _resolve(args)
            missing = p.get("_missing", [])
            if missing:
                return _unknown_profile(missing)
            patterns = _split_patterns(args.get("vacation_booking_patterns") or p.get("vacation_booking_patterns"))
            if not patterns:
                return json.dumps({
                    "action": "absences", "op": "import_from_bookings",
                    "error": "no vacation_booking_patterns configured",
                    "ask": (
                        "Ask the user for the vacation booking reference (may change per year — patterns are "
                        "additive), or take vacation days directly (op='add'), from a vault note, or extracted "
                        "from a document (Excel/PDF) they provide."
                    ),
                }, ensure_ascii=False)
            factor = float(args.get("vacation_hour_factor") or p.get("vacation_hour_factor") or 1.0)
            hours = wc.hours_per_day(
                float(p["weekly_hours"]), int(p["days_per_week"]), float(p.get("part_time_factor") or 1.0),
                work_weekdays=p.get("work_weekdays") or None,
            )
            where = _like_sql("reference_key", patterns)
            params: List[Any] = list(patterns)
            if args.get("start") or args.get("year"):
                start, end = _range(args)
                where += " AND substr(timestamp, 1, 10) BETWEEN ? AND ?"
                params += [start.isoformat(), end.isoformat()]
            booked = conn.execute(
                "SELECT substr(timestamp, 1, 10) AS day, SUM(duration_seconds) / 3600.0 "
                f"FROM mcp_records WHERE {where} AND duration_seconds > 0 GROUP BY 1",
                params,
            ).fetchall()
            # Refresh semantics (AIS-275): derived booking rows in the import
            # window are dropped first, so a cancelled/moved vacation booking
            # disappears instead of silently inflating vacation credit. Only
            # 'bookings:%' rows are touched — user/vault/document entries stay.
            delete_sql = f"DELETE FROM {ABSENCES_TABLE} WHERE kind = ? AND source LIKE 'bookings:%'"
            delete_params: List[Any] = [kind]
            if args.get("start") or args.get("year"):
                delete_sql += " AND day BETWEEN ? AND ?"
                delete_params += [start.isoformat(), end.isoformat()]
            deleted = conn.execute(delete_sql, delete_params).rowcount or 0
            source = "bookings:" + ", ".join(patterns)
            rows = [(day, min(1.0, round(booked_h * factor / hours, 4)), kind, source, None, now) for day, booked_h in booked]
            _upsert_absences(conn, rows)
            return json.dumps({
                "action": "absences", "op": "import_from_bookings", "patterns": patterns,
                "vacation_hour_factor": factor, "hours_per_day": hours, "upserted": len(rows),
                "deleted": deleted,
                "summary": _absences_summary(conn),
            }, ensure_ascii=False)

        return tool_error("unknown op; one of add, list, remove, import_from_bookings", success=False)
    finally:
        conn.close()


def _presence_summary(conn: sqlite3.Connection, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict[str, Any]]:
    where, params = "", []
    if start and end:
        where, params = "WHERE day BETWEEN ? AND ?", [start, end]
    rows = conn.execute(
        f"SELECT substr(day, 1, 7) AS month, kind, COUNT(*), GROUP_CONCAT(DISTINCT source) "
        f"FROM {PRESENCE_TABLE} {where} GROUP BY 1, 2 ORDER BY 1, 2",
        params,
    ).fetchall()
    return [{"month": m, "kind": k, "days": n, "sources": src} for m, k, n, src in rows]


def _like_pattern(value: str) -> str:
    text = str(value or "").strip()
    return text if "%" in text else f"%{text}%"


def _act_presence(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    """Where the user worked — the counterpart of ``absences`` (AIS-341).

    Kinds: office, homeoffice, travel. Days come from any calendar whose
    entries name the user (import), from the user directly, or from a
    document. A booked working day with no presence entry and no absence
    counts as the profile's ``presence_default`` (home office unless the
    user records home-office days instead of office days) — derived in
    ``report``, never stored.
    """
    op = str(args.get("op") or "list").strip().lower()
    kind = str(args.get("kind") or "office").strip().lower()
    if kind not in PRESENCE_KINDS:
        return tool_error(f"presence kind must be one of {', '.join(PRESENCE_KINDS)}", success=False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _open_db(db_path)
    try:
        if op == "list":
            return json.dumps({"action": "presence", "op": "list", "summary": _presence_summary(conn)}, ensure_ascii=False)

        if op == "add":
            source = str(args.get("source") or "user").strip()
            note = str(args.get("note") or "").strip() or None
            rows: List[tuple] = []
            for item in args.get("days") or []:
                if isinstance(item, dict) and (item.get("from") or item.get("to")):
                    d_from = wc.parse_iso_date(item.get("from"), "days.from")
                    d_to = wc.parse_iso_date(item.get("to") or item.get("from"), "days.to")
                    d = d_from
                    while d <= d_to:
                        rows.append((d.isoformat(), kind, source, note, now))
                        d += timedelta(days=1)
                elif isinstance(item, dict):
                    rows.append((wc.parse_iso_date(item.get("day"), "days.day").isoformat(), kind, source, note, now))
                else:
                    rows.append((wc.parse_iso_date(item, "days").isoformat(), kind, source, note, now))
            if not rows:
                return tool_error("op='add' needs days: ['YYYY-MM-DD', …] and/or [{'from': …, 'to': …}]", success=False)
            _upsert_presence(conn, rows)
            return json.dumps({"action": "presence", "op": "add", "upserted": len(rows), "kind": kind,
                               "source": source, "summary": _presence_summary(conn)}, ensure_ascii=False)

        if op == "remove":
            conditions, params = ["kind = ?"], [kind]
            days = [wc.parse_iso_date(d, "days").isoformat() for d in args.get("days") or []]
            if days:
                conditions.append("day IN (" + ",".join("?" * len(days)) + ")")
                params += days
            elif args.get("start") or args.get("year") or args.get("period"):
                start, end = _range(args)
                conditions.append("day BETWEEN ? AND ?")
                params += [start.isoformat(), end.isoformat()]
            elif args.get("source"):
                pass
            else:
                return tool_error("op='remove' needs days, start/end/year, or source — refusing to wipe the table", success=False)
            if args.get("source"):
                conditions.append("source LIKE ?")
                params.append(str(args["source"]))
            cur = conn.execute(f"DELETE FROM {PRESENCE_TABLE} WHERE " + " AND ".join(conditions), params)
            conn.commit()
            return json.dumps({"action": "presence", "op": "remove", "deleted": cur.rowcount,
                               "summary": _presence_summary(conn)}, ensure_ascii=False)

        if op == "import_from_calendar":
            profile = load_profile() or {}
            calendar = str(args.get("calendar") or profile.get("presence_calendar") or "").strip()
            patterns = _split_patterns(args.get("match") or profile.get("presence_match_patterns"))
            if not calendar or not patterns:
                return json.dumps({
                    "action": "presence", "op": "import_from_calendar",
                    "error": "presence calendar and match patterns are not configured",
                    "missing": [k for k, v in (("calendar", calendar), ("match", patterns)) if not v],
                    "ask": (
                        "Ask the user which calendar holds their presence days (a shared office calendar, a team "
                        "calendar, …) and how their entries are labelled (usually their name); pass calendar=… and "
                        "match=… (or persist via workdays(action='configure', presence_calendar=…, "
                        "presence_match_patterns=…)). kind= says what the entries mean (office, homeoffice, travel)."
                    ),
                }, ensure_ascii=False)
            tool_pattern = str(args.get("calendar_source_tool") or "%_events%")
            canonical = conn.execute(
                "SELECT reference_key FROM mcp_records WHERE tool_name LIKE ? AND lower(reference_key) = lower(?) LIMIT 1",
                (tool_pattern, calendar),
            ).fetchone()
            calendar = str(canonical[0]) if canonical and canonical[0] else calendar
            where = "tool_name LIKE ? AND lower(reference_key) = lower(?) AND substr(timestamp, 1, 10) <> ''"
            params: List[Any] = [tool_pattern, calendar]
            window = None
            if args.get("start") or args.get("year") or args.get("period"):
                start, end = _range(args)
                window = (start.isoformat(), end.isoformat())
                where += " AND substr(timestamp, 1, 10) BETWEEN ? AND ?"
                params += list(window)
            total, first, last = conn.execute(
                f"SELECT COUNT(*), MIN(substr(timestamp, 1, 10)), MAX(substr(timestamp, 1, 10)) FROM mcp_records WHERE {where}",
                params,
            ).fetchone()
            match_sql = _like_sql("raw_data", [_like_pattern(p) for p in patterns])
            days = [r[0] for r in conn.execute(
                f"SELECT DISTINCT substr(timestamp, 1, 10) FROM mcp_records WHERE {where} AND {match_sql} ORDER BY 1",
                params + [_like_pattern(p) for p in patterns],
            ).fetchall()]
            source = f"calendar:{calendar}"
            delete_sql = f"DELETE FROM {PRESENCE_TABLE} WHERE kind = ? AND lower(source) = lower(?)"
            delete_params: List[Any] = [kind, source]
            if window:
                delete_sql += " AND day BETWEEN ? AND ?"
                delete_params += list(window)
            deleted = conn.execute(delete_sql, delete_params).rowcount or 0
            _upsert_presence(conn, [(d, kind, source, None, now) for d in days])
            payload: Dict[str, Any] = {
                "action": "presence", "op": "import_from_calendar", "calendar": calendar, "match": patterns,
                "kind": kind, "upserted": len(days), "deleted": deleted,
                "coverage": {"events": total, "first_day": first, "last_day": last, "window": window},
                "summary": _presence_summary(conn, *(window or (None, None))),
            }
            if not total:
                payload["hints"] = [
                    f"no ingested events for calendar '{calendar}' in this window — fetch them first "
                    f"(e.g. m365_get_events(calendar='{calendar}', start_time_iso=…, end_time_iso=…); results auto-ingest "
                    "into mcp_records), then rerun this import"
                ]
            elif not days:
                payload["hints"] = [
                    f"{total} events found but none match {patterns} — check how the user's office entries are labelled"
                ]
            return json.dumps(payload, ensure_ascii=False)

        return tool_error("unknown op; one of add, list, remove, import_from_calendar", success=False)
    finally:
        conn.close()


def _upsert_presence(conn: sqlite3.Connection, rows: List[tuple]) -> None:
    conn.executemany(
        f"INSERT INTO {PRESENCE_TABLE} (day, kind, source, note, created_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(day, kind) DO UPDATE SET source=excluded.source, note=excluded.note, created_at=excluded.created_at",
        rows,
    )
    conn.commit()


def _act_estimate(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    conn = _open_db(db_path)
    try:
        sources = conn.execute(
            "SELECT tool_name, COUNT(*) AS n, MIN(substr(timestamp, 1, 10)), MAX(substr(timestamp, 1, 10)) "
            "FROM mcp_records WHERE duration_seconds > 0 AND timestamp IS NOT NULL "
            "GROUP BY tool_name ORDER BY n DESC LIMIT 5"
        ).fetchall()
        if not sources:
            return json.dumps({
                "action": "estimate_profile",
                "error": "no ingested worklog data to estimate from",
                "ask": "Ask the user directly for their work-time model: hours/week, working days (Mo-Fr, Mo-Sa, Mo-We, …), full/part time, region.",
                "clarify_choices": CLARIFY_CHOICES,
            }, ensure_ascii=False)
        top = sources[0][0]
        hist = conn.execute(
            "SELECT CAST(strftime('%w', substr(timestamp, 1, 10)) AS INTEGER) AS wd, "
            "COUNT(DISTINCT substr(timestamp, 1, 10)) FROM mcp_records "
            "WHERE tool_name = ? AND duration_seconds > 0 GROUP BY 1",
            (top,),
        ).fetchall()
        iso_hist = {((wd + 6) % 7) + 1: n for wd, n in hist}  # %w: 0=Sun → ISO 1=Mon
        max_booked = max(iso_hist.values())
        proposed_days = sorted(d for d, n in iso_hist.items() if n >= 0.2 * max_booked)
        monday = _today() - timedelta(days=_today().weekday())
        weeks = conn.execute(
            "SELECT strftime('%Y-%W', substr(timestamp, 1, 10)) AS wk, SUM(duration_seconds) / 3600.0 "
            "FROM mcp_records WHERE tool_name = ? AND duration_seconds > 0 AND substr(timestamp, 1, 10) < ? "
            "GROUP BY wk ORDER BY wk DESC LIMIT 8",
            (top, monday.isoformat()),
        ).fetchall()
        avg = round(sum(h for _, h in weeks) / len(weeks), 1) if weeks else None
        snapped = min((20.0, 25.0, 30.0, 38.5, 40.0, 42.0), key=lambda x: abs(x - avg)) if avg else None
        vacation_candidates = conn.execute(
            "SELECT reference_key, COUNT(*) AS n FROM mcp_records "
            "WHERE tool_name = ? AND duration_seconds BETWEEN 1 AND 7200 AND reference_key IS NOT NULL "
            "GROUP BY reference_key HAVING n >= 3 ORDER BY n DESC LIMIT 3",
            (top,),
        ).fetchall()
        profile = load_profile() or {}
        missing = [k for k in ("region", "weekly_hours", "days_per_week") if not profile.get(k)]
        proposal: Dict[str, Any] = {"worklog_source_tool": top}
        if proposed_days:
            proposal["work_weekdays"] = [_DAY_ABBR[d].lower() for d in proposed_days]
        if snapped is not None:
            proposal["weekly_hours"] = snapped
        return json.dumps({
            "action": "estimate_profile",
            "proposal": proposal,
            "evidence": {
                "sources": [{"tool_name": t, "rows": n, "first_day": f, "last_day": l} for t, n, f, l in sources],
                "weekday_booked_days": {_DAY_ABBR[d]: n for d, n in sorted(iso_hist.items())},
                "avg_weekly_hours_last_8_complete_weeks": avg,
            },
            "candidates": {"vacation_booking_patterns": [k for k, _ in vacation_candidates]},
            "missing": missing or ["confirmation"],
            "next": (
                "Present this proposal to the user in their language and ask them to CONFIRM or correct it "
                "(region is never estimated — ask for it; also ask full/part time, and when the region has "
                "municipal partial holidays, ask for municipality/PLZ too), then persist via "
                "workdays(action='configure', …)."
            ),
        }, ensure_ascii=False)
    finally:
        conn.close()


def _act_report(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    p = _resolve(args)
    missing = list(p.get("_missing", []))
    src_patterns = _split_patterns(p.get("worklog_source_tool"))
    if not src_patterns:
        missing.append("worklog_source_tool")
    if missing:
        return json.dumps({
            "error": "report needs a complete worktime profile",
            "missing": missing,
            "ask": (
                "Configure the missing keys with workdays(action='configure', …); worklog_source_tool is the LIKE "
                "pattern matching the user's time bookings in mcp_records (any worklog tool, not vendor-specific)."
            ),
            "estimate": "workdays(action='estimate_profile') proposes values from ingested data — confirm with the user first.",
        }, ensure_ascii=False)
    today = _today()
    period = str(args.get("period") or "").strip().lower() or None
    if period:
        emp = p.get("employment_start")
        start, end = _period_range(period, today, wc.parse_iso_date(emp, "employment_start") if emp else None)
    elif args.get("start") or args.get("year"):
        start, end = _range(args)
    else:
        emp = p.get("employment_start")
        start = wc.parse_iso_date(emp, "employment_start") if emp else date(today.year, 1, 1)
        end = today
    requested = {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True}
    if period:
        requested["period"] = period
    clamped = end > today
    if clamped:
        end = today
    if start > end:
        return tool_error(f"range starts in the future ({start.isoformat()}) — nothing to report yet", success=False)
    target_full_range = None
    if clamped:
        computed_full, err = _compute(dict(args, start=requested["start"], end=requested["end"]))
        if err:
            return err
        target_full_range = wc.totals(computed_full[5])["target_hours"]
    mat = json.loads(_act_materialize(dict(args, start=start.isoformat(), end=end.isoformat()), db_path=db_path))
    if mat.get("error"):
        return json.dumps(mat, ensure_ascii=False)

    vac_patterns = _split_patterns(p.get("vacation_booking_patterns"))
    ist_where = _like_sql("tool_name", src_patterns)
    ist_params: List[Any] = list(src_patterns)
    if vac_patterns:
        ist_where += " AND NOT " + _like_sql("reference_key", vac_patterns)
        ist_params += vac_patterns
    s, e = start.isoformat(), end.isoformat()
    ctes = f"""
        WITH ist AS (
            SELECT substr(timestamp, 1, 10) AS day, SUM(duration_seconds) / 3600.0 AS hours
            FROM mcp_records
            WHERE {ist_where} AND substr(timestamp, 1, 10) BETWEEN ? AND ?
            GROUP BY 1),
        vac AS (
            SELECT c.month AS month, SUM(a.portion * c.target_hours) AS hours
            FROM {ABSENCES_TABLE} a JOIN {TABLE} c ON c.day = a.day
            WHERE a.kind = 'vacation' AND a.day BETWEEN ? AND ?
            GROUP BY 1)
    """
    cte_params = ist_params + [s, e, s, e]
    conn = _open_db(db_path)
    try:
        month_rows = conn.execute(
            ctes + f"""
            SELECT c.month,
                   ROUND(SUM(c.target_hours), 2),
                   ROUND(COALESCE(MAX(v.hours), 0), 2),
                   ROUND(SUM(c.target_hours) - COALESCE(MAX(v.hours), 0), 2),
                   ROUND(COALESCE(SUM(i.hours), 0), 2),
                   ROUND(COALESCE(SUM(i.hours), 0) - (SUM(c.target_hours) - COALESCE(MAX(v.hours), 0)), 2)
            FROM {TABLE} c
            LEFT JOIN ist i ON i.day = c.day
            LEFT JOIN vac v ON v.month = c.month
            WHERE c.day BETWEEN ? AND ?
            GROUP BY c.month ORDER BY c.month""",
            cte_params + [s, e],
        ).fetchall()
        total = conn.execute(
            ctes + f"""
            SELECT tg, vc, ROUND(tg - vc, 2), act, ROUND(act - (tg - vc), 2) FROM (
                SELECT ROUND(SUM(c.target_hours), 2) AS tg,
                       ROUND(COALESCE((SELECT SUM(hours) FROM vac), 0), 2) AS vc,
                       ROUND(COALESCE((SELECT SUM(hours) FROM ist), 0), 2) AS act
                FROM {TABLE} c WHERE c.day BETWEEN ? AND ?)""",
            cte_params + [s, e],
        ).fetchone()
        coverage = []
        for pat in src_patterns:
            n, first, last, fetched = conn.execute(
                "SELECT COUNT(*), MIN(substr(timestamp, 1, 10)), MAX(substr(timestamp, 1, 10)), "
                "MAX(created_at) FROM mcp_records "
                "WHERE tool_name LIKE ? AND substr(timestamp, 1, 10) BETWEEN ? AND ?",
                (pat, s, e),
            ).fetchone()
            # last_fetched_at = when this local mirror was last refreshed from
            # upstream (UTC); an old value means the data may be stale even
            # though the range looks covered (AIS-275).
            coverage.append({
                "pattern": pat, "rows": n, "first_day": first, "last_day": last,
                "last_fetched_at": fetched,
            })
        absence_cov = conn.execute(
            f"SELECT source, COUNT(*), ROUND(SUM(portion), 2), MAX(created_at) FROM {ABSENCES_TABLE} "
            "WHERE kind = 'vacation' AND day BETWEEN ? AND ? GROUP BY source",
            (s, e),
        ).fetchall()
        presence_cov = conn.execute(
            f"SELECT source, kind, COUNT(*), MAX(created_at) FROM {PRESENCE_TABLE} "
            "WHERE day BETWEEN ? AND ? GROUP BY source, kind",
            (s, e),
        ).fetchall()
        day_rows = _report_day_rows(conn, ist_where, ist_params, s, e,
                                    presence_default=str(p.get("presence_default") or DEFAULT_PRESENCE))
    finally:
        conn.close()
    presence_months, presence_total = _presence_counts(day_rows)

    hints = []
    if all(c["rows"] == 0 for c in coverage):
        hints.append(
            f"no bookings in mcp_records match '{', '.join(src_patterns)}' for {s}..{e} — fetch them with the "
            "worklog tool matching that pattern (results auto-ingest into mcp_records), then rerun report"
        )
    else:
        last_booked = max((c["last_day"] for c in coverage if c["last_day"]), default=None)
        if last_booked and last_booked < (end - timedelta(days=7)).isoformat():
            hints.append(
                f"data_gap: bookings end at {last_booked} but the range ends {e} — fetch the missing tail, then rerun report"
            )
    if not absence_cov:
        hints.append(
            "no absences recorded for this range — import via workdays(action='absences', op='import_from_bookings'), "
            "add days directly (op='add'), or extract them from a vault note/document; vacation_credit is 0 until then"
        )

    if not presence_cov and (presence_total["office_days"] or presence_total["homeoffice_days"]):
        default_kind = str(p.get("presence_default") or DEFAULT_PRESENCE)
        hints.append(
            f"no presence recorded for this range — every booked working day counts as '{default_kind}' "
            "(profile presence_default) until workdays(action='presence', op='import_from_calendar', calendar=…, "
            "match=…, kind=…) or op='add' fills the presence table"
        )

    range_payload: Dict[str, Any] = {"start": s, "end": e, "inclusive": True}
    if period:
        range_payload["resolved_from"] = period
    payload: Dict[str, Any] = {
        "action": "report",
        "range": range_payload,
        "totals": {"target_gross": total[0], "vacation_credit": total[1], "target_net": total[2],
                   "actual": total[3], "delta": total[4], **presence_total},
        "months": [
            {"month": m, "target_gross": tg, "vacation_credit": vc, "target_net": tn, "actual": act, "delta": dl,
             **presence_months.get(m, _empty_presence())}
            for m, tg, vc, tn, act, dl in month_rows
        ],
        "assumptions": mat.get("assumptions"),
        "coverage": {
            "worklog_sources": coverage,
            "vacation_absences": [
                {"source": src, "days": n, "portions": pt, "last_updated_at": upd}
                for src, n, pt, upd in absence_cov
            ],
            "presence_sources": [
                {"source": src, "kind": k, "days": n, "last_updated_at": upd}
                for src, k, n, upd in presence_cov
            ],
        },
        "formula": FORMULA,
    }
    if args.get("include_days"):
        payload["days"] = day_rows
    if clamped:
        payload["clamped_to_today"] = True
        payload["requested_range"] = requested
        payload["target_full_range"] = target_full_range
    elif period:
        payload["requested_range"] = requested
    if hints:
        payload["hints"] = hints
    partial_hint = _partial_holidays_hint(p, start, end)
    if partial_hint:
        payload["partial_holidays_unresolved"] = partial_hint
    write = str(args.get("write") or "").strip().lower()
    if write:
        if write != "vault":
            return tool_error("write must be 'vault' (the only supported target)", success=False)
        payload["report_file"] = _write_report_to_vault(payload, day_rows, args, period, start, end, requested)
    payload["note"] = "Present results in the user's language."
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Report: per-day rows, presence counts, vault rendering (AIS-338 / AIS-341)
# ---------------------------------------------------------------------------

_STATUS_TOLERANCE_HOURS = 0.25


def _report_day_rows(conn: sqlite3.Connection, ist_where: str, ist_params: List[Any], s: str, e: str,
                     presence_default: str = DEFAULT_PRESENCE) -> List[Dict[str, Any]]:
    """One row per calendar day — first start, last end, hours, absence,
    presence — all derived in SQLite from workday_calendar, mcp_records,
    absences and presence. The model asked for "Start und Ende pro Tag" and
    had to build that by hand before (AIS-338)."""
    rows = conn.execute(
        f"""
        WITH ist AS (
            SELECT substr(timestamp, 1, 10) AS day,
                   SUM(duration_seconds) / 3600.0 AS hours,
                   COUNT(*) AS bookings,
                   MIN(CASE WHEN length(timestamp) >= 16 THEN substr(timestamp, 12, 5) END) AS first_start,
                   MAX(CASE WHEN length(timestamp) >= 16 THEN strftime('%H:%M', datetime(substr(timestamp, 1, 19), '+' || duration_seconds || ' seconds')) END) AS last_end,
                   GROUP_CONCAT(DISTINCT reference_key) AS refs
            FROM mcp_records
            WHERE {ist_where} AND substr(timestamp, 1, 10) BETWEEN ? AND ?
            GROUP BY 1),
        ab AS (
            SELECT day, SUM(portion) AS portion, GROUP_CONCAT(DISTINCT kind) AS kinds
            FROM {ABSENCES_TABLE} WHERE day BETWEEN ? AND ? GROUP BY day),
        pr AS (
            SELECT day, GROUP_CONCAT(DISTINCT kind) AS kinds
            FROM {PRESENCE_TABLE} WHERE day BETWEEN ? AND ? GROUP BY day)
        SELECT c.day, c.weekday, c.is_weekend, c.is_holiday, c.holiday_name, c.factor, c.target_hours,
               i.first_start, i.last_end, ROUND(COALESCE(i.hours, 0), 2), COALESCE(i.bookings, 0), i.refs,
               ab.portion, ab.kinds, pr.kinds
        FROM {TABLE} c
        LEFT JOIN ist i ON i.day = c.day
        LEFT JOIN ab ON ab.day = c.day
        LEFT JOIN pr ON pr.day = c.day
        WHERE c.day BETWEEN ? AND ?
        ORDER BY c.day""",
        ist_params + [s, e, s, e, s, e, s, e],
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for (day, weekday, is_weekend, is_holiday, holiday_name, factor, target, first_start, last_end,
         actual, bookings, refs, ab_portion, ab_kinds, pr_kinds) in rows:
        ab_portion = float(ab_portion or 0.0)
        target = float(target or 0.0)
        target_net = round(max(0.0, target - ab_portion * target), 2) if target else 0.0
        pr_set = {k for k in str(pr_kinds or "").split(",") if k}
        recorded = next((k for k in PRESENCE_KINDS if k in pr_set), None)
        if recorded:
            presence = recorded
        elif ab_portion >= 1.0:
            presence = (str(ab_kinds or "vacation").split(",")[0]) or "vacation"
        elif actual > 0 and target > 0:
            presence = presence_default
        else:
            presence = "none"
        if target == 0:
            status = "off_booked" if actual > 0 else "off"
        elif ab_portion >= 1.0:
            status = "absent"
        elif actual == 0:
            status = "no_booking"
        elif actual < target_net - _STATUS_TOLERANCE_HOURS:
            status = "short"
        elif actual > target_net + _STATUS_TOLERANCE_HOURS:
            status = "over"
        else:
            status = "ok"
        row: Dict[str, Any] = {
            "day": day, "weekday": _DAY_ABBR[int(weekday)], "target_hours": target, "target_net": target_net,
            "first_start": first_start, "last_end": last_end, "actual": float(actual or 0.0),
            "bookings": int(bookings or 0), "presence": presence, "status": status,
        }
        if is_weekend:
            row["weekend"] = True
        if is_holiday and holiday_name:
            row["holiday"] = holiday_name
        if factor not in (None, 0, 1) and not is_weekend and not is_holiday:
            row["factor"] = factor
        if ab_portion:
            row["absence"] = {"portion": round(ab_portion, 2), "kind": ab_kinds}
        if refs:
            row["references"] = refs
        out.append(row)
    return out


def _empty_presence() -> Dict[str, int]:
    return {"office_days": 0, "homeoffice_days": 0, "travel_days": 0, "absence_days": 0}


def _presence_counts(day_rows: List[Dict[str, Any]]) -> tuple:
    months: Dict[str, Dict[str, int]] = {}
    total = _empty_presence()
    key_for = {"office": "office_days", "homeoffice": "homeoffice_days", "travel": "travel_days"}
    for row in day_rows:
        month = row["day"][:7]
        bucket = months.setdefault(month, _empty_presence())
        key = key_for.get(row["presence"])
        if key is None and row.get("absence") and row["absence"].get("portion", 0) >= 1.0:
            key = "absence_days"
        if key:
            bucket[key] += 1
            total[key] += 1
    return months, total


_REPORT_LABELS = {
    "en": {
        "title": "Working time {period}", "assumptions": "Assumptions", "result": "Result", "days": "Days",
        "presence": "Presence", "coverage": "Coverage", "method": "Method", "verification": "Verification",
        "month": "Month", "target_gross": "Target (gross)", "vacation": "Vacation credit", "target_net": "Target (net)",
        "actual": "Actual", "delta": "Delta", "total": "Total", "day": "Day", "wd": "Wd", "start": "Start",
        "end": "End", "hours": "Hours", "status": "Status", "office": "Office", "homeoffice": "Home office",
        "travel": "Travel", "absence": "Absence", "note": "Note",
        "verified": "Sum of the daily actuals equals the monthly actuals; covered range {start} to {end}.",
        "regen": "Regenerate with: {call}", "range": "Range", "generated": "Generated",
    },
    "de": {
        "title": "Arbeitszeit {period}", "assumptions": "Annahmen", "result": "Ergebnis", "days": "Tage",
        "presence": "Präsenz", "coverage": "Datenabdeckung", "method": "Methode", "verification": "Prüfung",
        "month": "Monat", "target_gross": "Soll (brutto)", "vacation": "Urlaubsgutschrift", "target_net": "Soll (netto)",
        "actual": "Ist", "delta": "Differenz", "total": "Gesamt", "day": "Tag", "wd": "WT", "start": "Beginn",
        "end": "Ende", "hours": "Stunden", "status": "Status", "office": "Büro", "homeoffice": "Home-Office",
        "travel": "Auswärts", "absence": "Abwesenheit", "note": "Hinweis",
        "verified": "Summe der Tages-Istwerte entspricht den Monats-Istwerten; abgedeckter Zeitraum {start} bis {end}.",
        "regen": "Neu erzeugen mit: {call}", "range": "Zeitraum", "generated": "Erzeugt",
    },
}
_DAY_LABELS = {"de": {"Mo": "Mo", "Tu": "Di", "We": "Mi", "Th": "Do", "Fr": "Fr", "Sa": "Sa", "Su": "So"}}
_STATUS_MARK = {"ok": "✅", "over": "🟢", "short": "🟡", "no_booking": "🔴", "absent": "⚠", "off": "", "off_booked": "🟢"}


def _report_language() -> str:
    try:
        from agent.i18n import get_language

        lang = str(get_language() or "en").lower()
    except Exception:
        lang = "en"
    return lang if lang in _REPORT_LABELS else "en"


def _report_period_label(period: Optional[str], start: date, end: date) -> str:
    """Stable file-name part: a period keyword, a whole year, a whole month,
    otherwise the explicit dates."""
    if period:
        return period.replace("_", "-")
    if start == date(start.year, 1, 1) and end == date(start.year, 12, 31):
        return str(start.year)
    if start.day == 1 and end.month == start.month and end.year == start.year \
            and (end + timedelta(days=1)).month != end.month:
        return f"{start.year}-{start.month:02d}"
    return f"{start.isoformat()}_{end.isoformat()}"


def _fmt_h(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _render_report_markdown(payload: Dict[str, Any], day_rows: List[Dict[str, Any]], args: Dict[str, Any],
                            period: Optional[str], created: str, title: str, lang: str) -> str:
    """Markdown following ``_templates/report.md`` (frontmatter keys, sections
    Assumptions / Result / Method / Verification) — rendered from the SQL
    results, so the model never types the report itself (AIS-338)."""
    L = _REPORT_LABELS[lang]
    today = _today().isoformat()
    rng = payload["range"]
    a = payload.get("assumptions") or {}
    include_days = bool(args.get("include_days"))
    call_args = {k: v for k, v in args.items() if k in ("period", "start", "end", "year", "include_days", "write") and v not in (None, "")}
    call = "workdays(action='report', " + ", ".join(f"{k}={v!r}" for k, v in call_args.items()) + ")"
    lines = [
        "---",
        "type: report",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"created: {created}",
        f"updated: {today}",
        "status: generated",
        f"covers: {rng['start']}/{rng['end']}",
        f"source: {json.dumps(', '.join(c['pattern'] for c in payload['coverage']['worklog_sources']), ensure_ascii=False)}",
        "related_to: [\"[[reports/_hub]]\"]",
        "tags:",
        "  - report",
        "  - worktime",
        "---",
        "",
        f"# {title}",
        "",
        f"> [!note] {L['assumptions']}",
        f"> {a.get('region_name') or a.get('region')} · {a.get('weekly_hours')} h/{'Woche' if lang == 'de' else 'week'} · "
        f"{a.get('days_per_week')} {'Tage' if lang == 'de' else 'days'} ({a.get('hours_per_day')} h/{'Tag' if lang == 'de' else 'day'}) · "
        f"{'halbe Tage' if lang == 'de' else 'half days'}: {', '.join(a.get('half_days') or []) or '-'} · "
        f"{L['range']}: {rng['start']} – {rng['end']}",
        "",
        f"## {L['result']}",
        "",
        f"| {L['month']} | {L['target_gross']} | {L['vacation']} | {L['target_net']} | {L['actual']} | {L['delta']} | {L['office']} | {L['homeoffice']} | {L['travel']} | {L['absence']} |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in payload["months"]:
        lines.append(
            f"| {m['month']} | {_fmt_h(m['target_gross'])} | {_fmt_h(m['vacation_credit'])} | {_fmt_h(m['target_net'])} | "
            f"{_fmt_h(m['actual'])} | {_fmt_h(m['delta'])} | {m['office_days']} | {m['homeoffice_days']} | {m['travel_days']} | {m['absence_days']} |"
        )
    t = payload["totals"]
    lines.append(
        f"| **{L['total']}** | **{_fmt_h(t['target_gross'])}** | **{_fmt_h(t['vacation_credit'])}** | **{_fmt_h(t['target_net'])}** | "
        f"**{_fmt_h(t['actual'])}** | **{_fmt_h(t['delta'])}** | **{t['office_days']}** | **{t['homeoffice_days']}** | **{t['travel_days']}** | **{t['absence_days']}** |"
    )
    if include_days:
        day_names = _DAY_LABELS.get(lang, {})
        lines += ["", f"## {L['days']}", "",
                  f"| {L['day']} | {L['wd']} | {L['start']} | {L['end']} | {L['hours']} | {L['target_net']} | {L['presence']} | {L['status']} | {L['note']} |",
                  "|---|---|---|---|---:|---:|---|---|---|"]
        for d in day_rows:
            if d["status"] == "off" and d["actual"] == 0 and not d.get("holiday"):
                continue  # plain weekends stay out of the table
            note = d.get("holiday") or ""
            if d.get("absence"):
                note = (note + " · " if note else "") + f"{d['absence']['kind']} {d['absence']['portion']}"
            presence = L.get(d["presence"], d["presence"]) if d["presence"] != "none" else ""
            lines.append(
                f"| {d['day']} | {day_names.get(d['weekday'], d['weekday'])} | {d.get('first_start') or ''} | {d.get('last_end') or ''} | "
                f"{_fmt_h(d['actual']) if d['actual'] else ''} | {_fmt_h(d['target_net']) if d['target_net'] else ''} | {presence} | "
                f"{_STATUS_MARK.get(d['status'], '')} {d['status']} | {note} |"
            )
    lines += ["", f"## {L['coverage']}", ""]
    for c in payload["coverage"]["worklog_sources"]:
        lines.append(f"- `{c['pattern']}`: {c['rows']} rows, {c['first_day'] or '-'} – {c['last_day'] or '-'} ({L['generated'].lower()} {c['last_fetched_at'] or '-'})")
    for v in payload["coverage"]["vacation_absences"]:
        lines.append(f"- {L['absence']}: {v['source']} ({v['days']} d, {v['portions']} portions)")
    for pr in payload["coverage"]["presence_sources"]:
        lines.append(f"- {L['presence']}: {pr['source']} ({pr['kind']}, {pr['days']} d)")
    for h in payload.get("hints") or []:
        lines.append(f"- ⚠ {h}")
    lines += ["", f"## {L['method']}", "", L["regen"].format(call=f"`{call}`"), "", f"`{FORMULA}`", "",
              f"## {L['verification']}", "",
              f"> [!warning] {L['verification']}",
              f"> {L['verified'].format(start=rng['start'], end=rng['end'])}", ""]
    return "\n".join(lines)


def _write_report_to_vault(payload: Dict[str, Any], day_rows: List[Dict[str, Any]], args: Dict[str, Any],
                           period: Optional[str], start: date, end: date, requested: Dict[str, Any]) -> Dict[str, Any]:
    """Overwrite the canonical report file in the vault (``reports/worklog/
    worktime-<period>.md``, language-neutral name): same path on every rerun,
    ``created`` kept, ``updated`` bumped — no ``-final``/``-v2`` copies (AIS-338)."""
    try:
        from agent.memory_facade import workspace_root
        root = workspace_root()
    except Exception as exc:
        return {"written": False, "error": f"vault lookup failed: {exc}"}
    if root is None:
        return {"written": False, "error": "no vault configured (workspace root unknown) — nothing written"}
    req_start = wc.parse_iso_date(requested["start"], "start")
    req_end = wc.parse_iso_date(requested["end"], "end")
    label = _report_period_label(period, req_start, req_end)
    lang = _report_language()
    title = _REPORT_LABELS[lang]["title"].format(period=label if period is None else f"{label} ({payload['range']['start']} – {payload['range']['end']})")
    path = Path(root) / "reports" / "worklog" / f"worktime-{label}.md"
    created = _today().isoformat()
    existed = path.exists()
    if existed:
        try:
            import re as _re
            m = _re.search(r"^created:\s*(\S+)", path.read_text(encoding="utf-8"), _re.M)
            if m:
                created = m.group(1)
        except OSError:
            pass
    text = _render_report_markdown(payload, day_rows, args, period, created, title, lang)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return {"written": False, "path": str(path), "error": str(exc)}
    return {"written": True, "path": str(path), "overwritten": existed, "language": lang, "bytes": len(text.encode("utf-8"))}


ACTIONS = ("holidays", "workdays", "target_hours", "days", "report", "estimate_profile", "absences", "presence", "materialize", "configure", "profile")


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
        if action == "report":
            return _act_report(args, db_path=db_path)
        if action == "estimate_profile":
            return _act_estimate(args, db_path=db_path)
        if action == "absences":
            return _act_absences(args, db_path=db_path)
        if action == "presence":
            return _act_presence(args, db_path=db_path)
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
        "Deterministic calendar facts and work-time accounting: working days, public holidays for DE/AT/CH "
        "(per state/canton), target hours, half days (24./31.12.), week models incl. explicit work_weekdays "
        "(e.g. Mo-We), and a one-call actual-vs-target report.\n"
        "MANDATORY for target hours, overtime, working days, public holidays, bridge days: NEVER type calendars, weekday "
        "counts or holiday dates into SQL or prose, never compute Easter yourself.\n"
        "Actions: 'report' (THE one-call actual-vs-target balance up to today: target, actual from ingested worklogs "
        "in mcp_records via the profile's worklog_source_tool pattern, vacation credit from the absences table, "
        "delta, office/home-office days — all math in SQLite; include_days=true adds one row per day with first "
        "start, last end, hours, presence and status; period='ytd'|'mtd'|'this_month'|'last_month'|'this_week'|"
        "'last_week'|'through_last_week' resolves relative ranges deterministically; write='vault' renders the "
        "canonical Markdown report into the vault (reports/worklog/worktime-<period>.md, overwritten in place) — "
        "never write that file yourself), 'estimate_profile' (propose a week model from ingested worklog data when the "
        "profile is unknown — present the proposal and let the user CONFIRM before configure; region is never "
        "estimated), 'absences' (source-neutral vacation/sick store in state.db: op=add/list/remove/"
        "import_from_bookings — days can come from booking tickets, the user directly, a vault note, or a document), "
        "'presence' (office/homeoffice/travel days: op=add/list/remove/import_from_calendar — days from ingested "
        "calendar events (calendar=… + match=… patterns naming the user, kind=what those entries mean); a booked "
        "working day without presence entry or absence counts as the profile's presence_default in report), "
        "'target_hours' (per-month working days + target hours, default), 'days' (the same plus EVERY calendar day "
        "of the range in one call — ask once for the whole range, never month by month), 'workdays', 'holidays', "
        "'materialize' (writes table workday_calendar into ~/.hermes/state.db for manual sql JOINs — advanced path), "
        "'profile' (show the saved work-time profile), 'configure' (save region/week model/source patterns to "
        "memory as 'Arbeitszeit-Profil').\n"
        "If the answer is 'worktime profile unknown': try action='estimate_profile', present the proposal, and ask "
        "the user with `clarify` (in their language) — never assume a state or week model. Municipal/partial "
        "holidays (e.g. Augsburger Friedensfest — city of Augsburg only) deduct ONLY after the user confirmed "
        "them: when a result carries 'partial_holidays_unresolved', ask once (municipality/PLZ helps) and persist "
        "via configure partial_holidays=[…] or []. Present results in the user's language."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS), "description": "What to compute (default target_hours)."},
            "period": {"type": "string", "enum": list(PERIODS), "description": "Relative range resolved from today (ISO weeks, Monday first): ytd, mtd, this_month, last_month, this_week, last_week, through_last_week (1 Jan … last Sunday). Instead of start/end."},
            "write": {"type": "string", "enum": ["vault"], "description": "report only: also render the Markdown report into the vault (reports/worklog/worktime-<period>.md, one canonical file overwritten on rerun, headings in display.language)."},
            "calendar": {"type": "string", "description": "presence import: name of the ingested calendar (mcp_records.reference_key of the calendar tool's rows); profile default presence_calendar."},
            "match": {"type": "string", "description": "presence import: comma-separated LIKE patterns that mark the user's own entries (subject/attendees/organizer), usually their name; profile default presence_match_patterns."},
            "presence_calendar": {"type": "string", "description": "configure: calendar whose entries record where the user works (used by presence import)."},
            "presence_match_patterns": {"type": "string", "description": "configure: comma-separated patterns naming the user in that calendar."},
            "presence_default": {"type": "string", "enum": list(PRESENCE_KINDS), "description": "configure: what a booked working day counts as when no presence entry exists (default homeoffice; 'office' when the user records home-office days instead)."},
            "start": {"type": "string", "description": "Range start, YYYY-MM-DD (inclusive)."},
            "end": {"type": "string", "description": "Range end, YYYY-MM-DD (inclusive)."},
            "year": {"type": "integer", "description": "Whole year instead of start/end."},
            "region": {"type": "string", "description": "DE-BY, DE-BW, …, AT, AT-W, …, CH-ZH, … (or a name like 'Bayern'). Taken from the saved profile when omitted."},
            "weekly_hours": {"type": "number", "description": "Contract hours per week (profile default)."},
            "days_per_week": {"type": "integer", "enum": [5, 6, 7], "description": "Working days per week (profile default). For other models pass work_weekdays instead."},
            "work_weekdays": {"type": "array", "items": {"type": "string"}, "description": "Explicit working weekdays for models like Mo-We: mo,tu,we,th,fr,sa,su (German di/mi/do/so or ISO 1-7). Overrides days_per_week."},
            "employment_label": {"type": "string", "enum": ["vollzeit", "teilzeit"], "description": "configure only: full/part-time label shown in assumptions (math stays weekly_hours/part_time_factor)."},
            "part_time_factor": {"type": "number", "description": "0 < factor <= 1 (default 1)."},
            "worklog_source_tool": {"type": "string", "description": "SQL LIKE pattern (comma-separated for several) matching mcp_records.tool_name rows that are the user's time bookings — any worklog tool, not vendor-specific. Needed for report/estimate."},
            "vacation_booking_patterns": {"type": "string", "description": "LIKE pattern(s) on mcp_records.reference_key for vacation bookings (the ticket workaround; may differ per year — patterns are additive)."},
            "vacation_hour_factor": {"type": "number", "description": "Credit hours per booked vacation hour for absences import (default 1.0; e.g. 8.0 when 1h booked = one 8h day)."},
            "op": {"type": "string", "enum": ["add", "list", "remove", "import_from_bookings", "import_from_calendar"], "description": "absences / presence: what to do (default list). import_from_bookings = absences, import_from_calendar = presence."},
            "days": {"type": "array", "items": {}, "description": "absences add/remove: 'YYYY-MM-DD' strings, {day, portion} objects, or {from, to} ranges (ranges expand to working days only)."},
            "portion": {"type": "number", "description": "absences add: fraction of a day per entry, 0 < portion <= 1 (default 1.0)."},
            "kind": {"type": "string", "description": "absences: vacation (default), sick, or other. presence: office (default), homeoffice, or travel."},
            "source": {"type": "string", "description": "absences: where the days came from, e.g. 'user', 'document:<id>', 'vault:<note>' (default user)."},
            "note": {"type": "string", "description": "absences add: free-text note stored with the entries."},
            "half_days": {"type": "array", "items": {"type": "string"}, "description": "MM-DD or YYYY-MM-DD days counted as half a working day (profile default: 12-24, 12-31)."},
            "extra_holidays": {"type": "array", "items": {"type": "string"}, "description": "Additional company holidays, YYYY-MM-DD."},
            "municipality": {"type": "string", "description": "configure: the user's city/municipality — evidence for whether municipal partial holidays apply (e.g. 'Augsburg')."},
            "plz": {"type": "string", "description": "configure: postal code (4-6 digits) — evidence for municipal partial holidays (Augsburg = 86150-86199)."},
            "partial_holidays": {"type": "array", "items": {"type": "string"}, "description": "Names of the region's municipal/partial holidays that APPLY to this user (deducted like statutory once confirmed); [] = user confirmed none apply. Never set without asking the user."},
            "employment_start": {"type": "string", "description": "YYYY-MM-DD; days before it carry no target time."},
            "employment_end": {"type": "string", "description": "YYYY-MM-DD; days after it carry no target time."},
            "include_days": {"type": "boolean", "description": "Also return one entry per calendar day (report: with first start, last end, hours, presence, status)."},
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
