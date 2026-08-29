"""Prompt-cache report from the per-call ``api_calls`` table.

Session totals hide the two things that decide the hit rate: whether the
gateway served the same model on every call (a switch throws the provider
cache away) and how the first call — always a full write — skews short
sessions. This reads the rows ``record_api_call`` writes and explains the
worst sessions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _hit(read: int, inp: int, write: int) -> float:
    total = read + inp + write
    return (read / total * 100.0) if total > 0 else 0.0


def session_stats(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    calls = sorted(calls, key=lambda c: (c.get("ts") or 0, c.get("id") or 0))
    n = len(calls)
    read = sum(int(c.get("cache_read_tokens") or 0) for c in calls)
    inp = sum(int(c.get("input_tokens") or 0) for c in calls)
    write = sum(int(c.get("cache_write_tokens") or 0) for c in calls)
    steady = calls[1:]
    s_read = sum(int(c.get("cache_read_tokens") or 0) for c in steady)
    s_inp = sum(int(c.get("input_tokens") or 0) for c in steady)
    s_write = sum(int(c.get("cache_write_tokens") or 0) for c in steady)
    served = [str(c.get("served_model") or "") for c in calls]
    switches = sum(1 for a, b in zip(served, served[1:]) if a and b and a != b)
    reasons: List[str] = []
    if n <= 2:
        reasons.append("only 1–2 calls (first call is always a cache write)")
    if switches:
        reasons.append(f"served model changed {switches}× ({' → '.join(dict.fromkeys(m for m in served if m))})")
    if steady and s_write > s_read:
        reasons.append("more cache writes than reads after the first call (prefix keeps changing)")
    if steady and s_inp > max(s_read, 1) * 2:
        reasons.append("uncached input dominates (conversation not covered by breakpoints)")
    return {
        "session_id": calls[0].get("session_id") if calls else "",
        "calls": n,
        "hit_pct": round(_hit(read, inp, write), 1),
        "steady_hit_pct": round(_hit(s_read, s_inp, s_write), 1) if steady else None,
        "input_tokens": inp,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "uncached_per_call": int(inp / n) if n else 0,
        "served_models": list(dict.fromkeys(m for m in served if m)),
        "model_switches": switches,
        "avg_latency_ms": int(sum(int(c.get("latency_ms") or 0) for c in calls) / n) if n else 0,
        "first_ts": calls[0].get("ts") if calls else None,
        "reasons": reasons,
    }


def cache_report(db, *, days: int = 7, session_id: Optional[str] = None) -> Dict[str, Any]:
    rows = db.get_api_calls(session_id=session_id, days=None if session_id else days)
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_session.setdefault(str(r.get("session_id") or ""), []).append(r)
    sessions = [session_stats(c) for c in by_session.values()]
    sessions.sort(key=lambda s: (s["first_ts"] or 0))
    read = sum(s["cache_read_tokens"] for s in sessions)
    inp = sum(s["input_tokens"] for s in sessions)
    write = sum(s["cache_write_tokens"] for s in sessions)
    steady_sessions = [s for s in sessions if s["steady_hit_pct"] is not None]
    worst = sorted([s for s in sessions if s["calls"] >= 3], key=lambda s: s["hit_pct"])[:5]
    return {
        "days": days,
        "session_id": session_id,
        "calls": len(rows),
        "sessions": sessions,
        "overall_hit_pct": round(_hit(read, inp, write), 1),
        "steady_hit_pct": round(
            sum(s["steady_hit_pct"] for s in steady_sessions) / len(steady_sessions), 1
        ) if steady_sessions else None,
        "sessions_with_switches": sum(1 for s in sessions if s["model_switches"]),
        "worst": worst,
    }


def format_cache_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    scope = f"session {report['session_id']}" if report.get("session_id") else f"last {report['days']} days"
    lines.append(f"Prompt cache — {scope}: {report['calls']} calls, {len(report['sessions'])} sessions")
    if not report["calls"]:
        lines.append("  no api_calls rows yet (recorded per request since this version)")
        return "\n".join(lines)
    steady = report["steady_hit_pct"]
    lines.append(
        f"  hit rate {report['overall_hit_pct']}% overall"
        + (f", {steady}% steady-state (first call of each session excluded)" if steady is not None else "")
        + f"; {report['sessions_with_switches']} session(s) with a served-model switch"
    )
    if report.get("session_id"):
        for s in report["sessions"]:
            lines.append(
                f"  calls {s['calls']} · hit {s['hit_pct']}% · steady {s['steady_hit_pct']} · uncached/call {s['uncached_per_call']:,} · "
                f"served {', '.join(s['served_models']) or '?'} · switches {s['model_switches']} · latency {s['avg_latency_ms']} ms"
            )
            for r in s["reasons"]:
                lines.append(f"    ↳ {r}")
        return "\n".join(lines)
    if report["worst"]:
        lines.append("  worst sessions (≥ 3 calls):")
        for s in report["worst"]:
            when = datetime.fromtimestamp(s["first_ts"]).strftime("%m-%d %H:%M") if s["first_ts"] else "?"
            lines.append(
                f"    {s['session_id']} ({when}) hit {s['hit_pct']}% · {s['calls']} calls · "
                f"uncached/call {s['uncached_per_call']:,} · served {', '.join(s['served_models']) or '?'}"
            )
            for r in s["reasons"]:
                lines.append(f"      ↳ {r}")
    return "\n".join(lines)


__all__ = ["cache_report", "format_cache_report", "session_stats"]
