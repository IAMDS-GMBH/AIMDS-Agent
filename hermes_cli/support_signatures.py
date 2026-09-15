"""Incident signatures and the support-bundle digest (AIS-344, B2).

Support bundles used to be raw log tails (400–1200 lines per file, no error
extraction, no diagnosis in the metadata), so every case meant reading the
whole bundle. This module turns the logs into a **digest**: a deterministic
catalog of log signatures (``SIGNATURES``) is matched against the recent part
of every log, the hits are listed with a little context and counted per
signature, and the counts land in ``metadata.json`` as ``issue_details.signals``
so the support tool can show the diagnosis before anyone opens a file.

Design rules:

* Signatures are log **patterns**, never user or customer data.
* Everything is deterministic — the same logs yield the same digest.
* The catalog is generic (boot, update, MCP, Graph, worktime, Python) and
  extensible: add a :class:`Signature`, nothing else changes. The desktop's
  ``apps/desktop/electron/boot-signatures.cjs`` carries a JS copy of the boot
  subset; a test pins its ids to this list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "SIGNATURES",
    "SIGNATURE_IDS",
    "Signature",
    "SignatureHit",
    "IncidentDigest",
    "build_incident_digest",
    "classify_text",
    "last_boot_section",
    "signal_summary",
]

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass(frozen=True)
class Signature:
    id: str
    regex: str
    title: str
    hint: str
    severity: str = "medium"

    @property
    def pattern(self) -> re.Pattern[str]:
        return _compiled(self.regex)


_COMPILED: dict[str, re.Pattern[str]] = {}


def _compiled(regex: str) -> re.Pattern[str]:
    pat = _COMPILED.get(regex)
    if pat is None:
        pat = re.compile(regex, re.IGNORECASE)
        _COMPILED[regex] = pat
    return pat


# Order matters only for ties in the digest listing; matching tries every
# signature on every line and records the *first* match per line, so put the
# specific patterns before the generic ones.
SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "boot.port_in_use",
        r"address already in use|EADDRINUSE|Errno 10048|\[Errno 48\]|\[Errno 98\]",
        "Backend port already in use",
        "A previous backend (or a child that inherited its socket) still held the port after a restart; "
        "the desktop now excludes failed ports, waits for the old port to be released and backs off between attempts.",
        "high",
    ),
    Signature(
        "boot.backend_exited_before_ready",
        r"backend exited before it became ready",
        "Backend exited before it became ready",
        "The Hermes backend process died during start-up — look for the Python error or a port conflict just above.",
        "high",
    ),
    Signature(
        "boot.backend_not_ready",
        r"backend did not become ready|Desktop boot failed|Backend bind race detected",
        "Desktop boot failed or retried",
        "The desktop could not reach the backend in time; check the spawn attempts and the port signatures.",
        "high",
    ),
    Signature(
        "update.handoff",
        r"launched updater:|launched mac swap\+relaunch|exiting desktop to release venv shim",
        "Update hand-off to the installer",
        "The desktop quit to let the staged updater run; boot errors right after this line are restart problems.",
        "info",
    ),
    Signature(
        "update.failed",
        r"hand-off failed|\[updates\][^\n]*(?:failed|error)|update failed|hermes update[^\n]*(?:failed|error)",
        "Update failed",
        "The update itself reported an error — read update.log / updater-launch.log around the hit.",
        "high",
    ),
    Signature(
        "update.web_build_failed",
        r"Web UI build failed",
        "Web dashboard build failed during update",
        "The updater served the stale web dist; the TypeScript error follows the hit.",
        "medium",
    ),
    Signature(
        "mcp.server_start_failed",
        r"MCP server[^\n]*(?:failed to start|could not start|exited)|failed to start MCP|mcp[^\n]*(?:spawn|launch)[^\n]*(?:failed|error)",
        "MCP server failed to start",
        "An MCP server did not come up; mcp-stderr.log carries the server's own output.",
        "medium",
    ),
    Signature(
        "mcp.tools_missing",
        r"registered 0 tools|no tools (?:were )?registered|tools? (?:missing|not found) for (?:server|mcp)",
        "MCP server registered no tools",
        "The server started but exposed no tools — usually an install or auth problem in the server.",
        "medium",
    ),
    Signature(
        "litellm.key_rejected",
        r"AIMDS-Suite 403|virtual key was rejected|PermissionDeniedError \[HTTP 403\]|Non-retryable error \(HTTP 403\)",
        "AIMDS-Suite virtual key rejected (HTTP 403)",
        "LiteLLM refused the client's virtual key (expired or wrong environment): re-authenticate via Settings → Providers → AIMDS-Suite (Keycloak SSO) and check the provider host.",
        "medium",
    ),
    Signature(
        "graph.403_consent",
        # Only Graph: LiteLLM/nginx 403 pages carry no Graph marker (AIS-345).
        r"AADSTS65001|consent_required|graph\.microsoft\.com[^\n]*403|403[^\n]*(?:Graph|consent)",
        "Microsoft Graph consent missing",
        "The tenant has not consented to the required Graph scopes; the M365 consent tier in the hit names the scope.",
        "medium",
    ),
    Signature(
        "graph.group_uri",
        r"ErrorGroupIsUsedInNonGroupURI",
        "Group mailbox addressed as a user mailbox",
        "The address is a Microsoft 365 group; group calendars need the groups consent tier (Group.Read.All).",
        "medium",
    ),
    Signature(
        "graph.400_invalid_id",
        r"ErrorInvalidIdMalformed|Graph[^\n]*400[^\n]*(?:invalid|malformed)",
        "Microsoft Graph rejected an id",
        "A calendar, mailbox or message id was malformed — usually a name was passed where an id was expected.",
        "low",
    ),
    Signature(
        "worktime.profile_unknown",
        r"workdays[^\n]*profile[^\n]*unknown|profile unknown",
        "Worktime profile unknown",
        "The workdays profile could not be loaded from memory; check the memory MCP and the state.db mirror.",
        "low",
    ),
    Signature(
        "python.traceback",
        r"Traceback \(most recent call last\)",
        "Python traceback",
        "An unhandled Python exception; the lines after the hit carry the stack.",
        "medium",
    ),
    Signature(
        "generic.error",
        r"\b(?:ERROR|CRITICAL)\b|\berror\b",
        "Error lines",
        "Error-level lines without a more specific signature; useful as a count, read the context when nothing else matches.",
        "low",
    ),
)

SIGNATURE_IDS: tuple[str, ...] = tuple(s.id for s in SIGNATURES)
_BY_ID = {s.id: s for s in SIGNATURES}

# Hits that are expected noise when one of the preceding lines matches: the
# strict-server probe deliberately calls a tool that does not exist, and
# openproject_ce_mcp answers with a traceback on stderr (AIS-345).
_SUPPRESSED_AFTER: dict[str, re.Pattern[str]] = {
    "python.traceback": re.compile(r"__strict_mcpserver_probe__"),
}
_SUPPRESS_LOOKBACK = 1


def _suppressed(signature_id: str, previous: list[str]) -> bool:
    pattern = _SUPPRESSED_AFTER.get(signature_id)
    return pattern is not None and any(pattern.search(line) for line in previous[-_SUPPRESS_LOOKBACK:])


@dataclass
class SignatureHit:
    signature: str
    file: str
    line_no: int
    line: str
    timestamp: str | None = None


@dataclass
class IncidentDigest:
    text: str
    signals: list[dict[str, Any]]
    hits: list[SignatureHit] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def top_signature(self) -> str | None:
        return self.signals[0]["id"] if self.signals else None


def classify_text(text: str) -> Signature | None:
    """First signature that matches anywhere in ``text``, most specific first.

    ``generic.error`` only wins when nothing else matches.
    """
    fallback: Signature | None = None
    for sig in SIGNATURES:
        if sig.pattern.search(text or ""):
            if sig.id == "generic.error":
                fallback = sig
                continue
            return sig
    return fallback


def _classify_line(line: str) -> Signature | None:
    for sig in SIGNATURES:
        if sig.pattern.search(line):
            return sig
    return None


_TS_RES: tuple[re.Pattern[str], ...] = (
    # 2026-09-15 11:13:02,123  /  2026-09-15T11:13:02.123Z  /  2026-09-15 11:13:02
    re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(Z|[+-]\d{2}:?\d{2})?"),
    # [15.09.2026 11:13:02] (updater / desktop on some locales)
    re.compile(r"(\d{2})\.(\d{2})\.(\d{4})[ T](\d{2}:\d{2}:\d{2})"),
)


def _line_timestamp(line: str) -> datetime | None:
    m = _TS_RES[0].search(line[:64])
    if m:
        try:
            base = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
        except ValueError:
            return None
        tz = m.group(3)
        if tz == "Z":
            return base.replace(tzinfo=timezone.utc)
        if tz:
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
            return base.replace(tzinfo=timezone(sign * offset))
        return base.replace(tzinfo=None)
    m = _TS_RES[1].search(line[:64])
    if m:
        try:
            return datetime.fromisoformat(f"{m.group(3)}-{m.group(2)}-{m.group(1)}T{m.group(4)}")
        except ValueError:
            return None
    return None


_BOOT_MARKER = re.compile(r"Resolving Hermes backend|\[boot\] Resolving|Desktop starting|=== desktop start", re.IGNORECASE)


def last_boot_section(lines: list[str], *, max_lines: int = 300) -> list[str]:
    """Lines from the last boot marker on (desktop.log has no timestamps).

    Falls back to the last ``max_lines`` lines when no marker is present.
    """
    start = 0
    for idx in range(len(lines) - 1, -1, -1):
        if _BOOT_MARKER.search(lines[idx]):
            start = idx
            break
    section = lines[start:]
    return section[-max_lines:]


def _within_window(
    lines: list[str], window: timedelta, now: datetime, *, untimestamped_lines: int = 1500
) -> list[tuple[int, str, datetime | None]]:
    """(line_no, line, timestamp) for lines within the window.

    Files without any timestamp keep their last boot section instead (see
    :func:`last_boot_section`). Lines without a timestamp inside a timestamped
    file (continuations, tracebacks) inherit the last seen timestamp.
    """
    stamped: list[tuple[int, str, datetime | None]] = []
    last_ts: datetime | None = None
    any_ts = False
    for idx, line in enumerate(lines, start=1):
        ts = _line_timestamp(line)
        if ts is not None:
            any_ts = True
            last_ts = ts
        stamped.append((idx, line, ts if ts is not None else last_ts))
    if not any_ts:
        # No timestamps (desktop.log): the last boot section alone would hide
        # the failed boots *before* the one that finally worked — exactly the
        # restart-hang evidence — so scan the last ``untimestamped_lines`` and
        # let the dedupe fold the repeats.
        section = lines[-untimestamped_lines:]
        offset = len(lines) - len(section)
        return [(offset + i + 1, line, None) for i, line in enumerate(section)]
    cutoff_naive = (now.replace(tzinfo=None) - window)
    cutoff_aware = now.astimezone(timezone.utc) - window
    out: list[tuple[int, str, datetime | None]] = []
    for idx, line, ts in stamped:
        if ts is None:
            continue
        if ts.tzinfo is None:
            if ts >= cutoff_naive:
                out.append((idx, line, ts))
        elif ts.astimezone(timezone.utc) >= cutoff_aware:
            out.append((idx, line, ts))
    return out


def _read_tail(path: Path, max_lines: int) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-max_lines:]]


def build_incident_digest(
    log_dir: Path,
    *,
    log_names: Iterable[str] | None = None,
    window_minutes: int = 60,
    max_lines: int = 400,
    context_lines: int = 3,
    max_repeats: int = 3,
    scan_lines: int = 5000,
    untimestamped_lines: int = 1500,
    max_line_chars: int = 400,
    now: datetime | None = None,
    redact=None,
) -> IncidentDigest:
    """Scan the recent part of every log for signatures.

    * ``window_minutes``: how far back timestamped logs are read; files without
      timestamps (desktop.log) contribute their last ``untimestamped_lines``.
    * ``max_lines``: overall cap on digest lines.
    * Identical hit lines (timestamp stripped) are listed at most ``max_repeats``
      times per file, then folded into one "… ×N" line.
    * ``redact``: optional ``str -> str`` applied to every emitted line.
    """
    now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=max(1, int(window_minutes)))
    names = list(log_names) if log_names is not None else sorted(p.name for p in log_dir.glob("*.log") if p.is_file())
    counts: dict[str, dict[str, Any]] = {}
    hits: list[SignatureHit] = []
    sections: list[str] = []
    files_meta: list[dict[str, Any]] = []
    emitted = 0
    redact = redact or (lambda s: s)

    for name in names:
        path = log_dir / name
        if not path.is_file():
            continue
        lines = _read_tail(path, scan_lines)
        if not lines:
            continue
        recent = _within_window(lines, window, now, untimestamped_lines=untimestamped_lines)
        if not recent:
            continue
        by_no = {no: line for no, line, _ in recent}
        # Classify once up front so a hit that lands inside another hit's
        # context window still gets its marker.
        hit_ids: dict[int, str] = {}
        previous: list[str] = []
        for no, line, _ in recent:
            matched = _classify_line(line)
            if matched is not None and not _suppressed(matched.id, previous):
                hit_ids[no] = matched.id
            previous.append(line)
            if len(previous) > _SUPPRESS_LOOKBACK:
                del previous[0]
        file_lines: list[str] = []
        repeats: dict[str, int] = {}
        skipped_by_key: dict[str, list[int]] = {}
        emitted_nos: set[int] = set()
        last_emitted_no = 0
        file_hits = 0
        for no, line, ts in recent:
            sig = _BY_ID.get(hit_ids.get(no, ""))
            if sig is None:
                continue
            file_hits += 1
            entry = counts.setdefault(
                sig.id,
                {"id": sig.id, "title": sig.title, "hint": sig.hint, "severity": sig.severity, "count": 0, "first": None, "last": None, "files": []},
            )
            entry["count"] += 1
            stamp = ts.isoformat(timespec="seconds") if ts else None
            if entry["first"] is None:
                entry["first"] = stamp
            entry["last"] = stamp
            if name not in entry["files"]:
                entry["files"].append(name)
            hits.append(SignatureHit(sig.id, name, no, line, stamp))
            key = _dedupe_key(redact(line))
            repeats[key] = repeats.get(key, 0) + 1
            if repeats[key] > max_repeats:
                skipped_by_key.setdefault(key, []).append(no)
                continue
            lo = max(no - context_lines, last_emitted_no + 1)
            hi = no + context_lines
            if lo > last_emitted_no + 1 and last_emitted_no:
                file_lines.append("  …")
            for ctx_no in range(lo, hi + 1):
                ctx = by_no.get(ctx_no)
                if ctx is None:
                    continue
                marker = f"[{hit_ids[ctx_no]}] " if ctx_no in hit_ids else ""
                shown = redact(ctx)
                if len(shown) > max_line_chars:
                    shown = shown[:max_line_chars] + " …"
                file_lines.append(f"  {ctx_no:>6}: {marker}{shown}")
                emitted_nos.add(ctx_no)
            last_emitted_no = hi
        # Repeats that never made it into the listing (not even as context).
        for key, nos in skipped_by_key.items():
            hidden = sum(1 for n in nos if n not in emitted_nos)
            if hidden:
                file_lines.append(f"  … {key[:120]} ×{hidden} more")
        if not file_hits:
            continue
        files_meta.append({"name": name, "hits": file_hits, "lines_scanned": len(recent)})
        header = f"## {name} ({file_hits} hits in {len(recent)} recent lines)"
        budget = max_lines - emitted
        if budget <= 0:
            sections.append(f"{header}\n  … digest line budget exhausted")
            continue
        if len(file_lines) > budget:
            file_lines = file_lines[-budget:]
            file_lines.insert(0, "  … (truncated to the newest hits)")
        emitted += len(file_lines)
        sections.append("\n".join([header, *file_lines]))

    # Severity, then count, then catalog order (specific before generic).
    catalog_index = {s.id: i for i, s in enumerate(SIGNATURES)}
    signals = sorted(
        counts.values(),
        key=lambda e: (-_SEVERITY_RANK.get(e["severity"], 0), -e["count"], catalog_index.get(e["id"], 999)),
    )
    head = [
        f"# Incident digest — {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"window: last {int(window.total_seconds() // 60)} min of timestamped logs; logs without timestamps: last {untimestamped_lines} lines",
        "",
        "## Signals",
    ]
    if signals:
        for s in signals:
            span = f" ({s['first']} … {s['last']})" if s.get("first") else ""
            head.append(f"- {s['id']} ×{s['count']} [{s['severity']}] {s['title']}{span} — {', '.join(s['files'])}")
            head.append(f"    hint: {s['hint']}")
    else:
        head.append("- none (no signature matched in the window)")
    text = "\n".join(head + [""] + sections) + "\n"
    return IncidentDigest(text=text, signals=signals, hits=hits, files=files_meta)


_TS_STRIP = re.compile(r"^\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s\]]*\]?\s*")


def _dedupe_key(line: str) -> str:
    return re.sub(r"\d+", "#", _TS_STRIP.sub("", line.strip()))[:160]


def signal_summary(signals: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    """Compact form for ``metadata.json`` — top ``limit`` signals, no hints."""
    out: list[dict[str, Any]] = []
    for s in signals[:limit]:
        out.append(
            {
                "id": s["id"],
                "count": s["count"],
                "severity": s["severity"],
                "title": s["title"],
                "first": s.get("first"),
                "last": s.get("last"),
                "files": list(s.get("files") or []),
            }
        )
    return out
