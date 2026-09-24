"""OpenProject MCP server (AIS-408).

A lean replacement for the upstream ``openproject-ce-mcp`` package: eleven
consolidated tools instead of ~150, and every failure is a ``ToolError`` whose
message says what to do next. Upstream turned "project 'PM' not found" into
"Error executing tool create_work_package"; the model then guessed, filed the
ticket in the wrong project, created three duplicates and could neither move
nor delete them (SUP-20260924-073844).

Configuration (same variables as the upstream package, so existing installs
keep their credentials):

- ``OPENPROJECT_BASE_URL``     https://openproject.example.com
- ``OPENPROJECT_API_TOKEN``    API token (My account -> Access tokens)
- ``OPENPROJECT_READ_PROJECTS``  comma-separated identifiers/names/ids/globs, ``*`` (default) = all visible
- ``OPENPROJECT_WRITE_PROJECTS`` same syntax; empty = read-only (write tools are not registered)
- ``OPENPROJECT_TIMEOUT``      seconds per request (default 20)

Every write validates through OpenProject's form endpoint first and returns a
preview; it only executes when called again with ``confirm=true``.
Text written by OpenProject users (descriptions, comments) is returned inside
``<user-content>`` tags: it is data, never instructions.
"""

from __future__ import annotations

import datetime as dt
import difflib
import fnmatch
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("OpenProjectMCP")
# httpx logs every request at INFO; that is noise in Hermes' mcp-stderr.log.
logging.getLogger("httpx").setLevel(logging.WARNING)

_USER_AGENT = "hermes-openproject-mcp/1.0"
_PAGE_SIZE_MAX = 100
_DESCRIPTION_LIMIT = 6000
_COMMENT_LIMIT = 1500
_LIST_TEXT_LIMIT = 200
_TIME_ENTRY_LIMIT = 2000
_DUPLICATE_WINDOW_S = 15 * 60
_CACHE_TTL_S = 300.0


# ─── Configuration ────────────────────────────────────────────────────────────


def _patterns(raw: Optional[str], default: str) -> List[str]:
    text = default if raw is None else raw
    return [p.strip().casefold() for p in text.split(",") if p.strip()]


def _config() -> Dict[str, Any]:
    base = (os.environ.get("OPENPROJECT_BASE_URL") or "").strip().rstrip("/")
    if base.endswith("/api/v3"):
        base = base[: -len("/api/v3")]
    try:
        timeout = max(1.0, float(os.environ.get("OPENPROJECT_TIMEOUT") or 20))
    except ValueError:
        timeout = 20.0
    return {
        "base_url": base,
        "token": (os.environ.get("OPENPROJECT_API_TOKEN") or "").strip(),
        "read": _patterns(os.environ.get("OPENPROJECT_READ_PROJECTS"), "*") or ["*"],
        "write": _patterns(os.environ.get("OPENPROJECT_WRITE_PROJECTS"), ""),
        "timeout": timeout,
    }


def writes_enabled() -> bool:
    return bool(_config()["write"])


# ─── HTTP ─────────────────────────────────────────────────────────────────────


_client_lock = threading.Lock()
_client: Optional[httpx.Client] = None
_client_key: Optional[Tuple[str, str, float]] = None


def _http() -> httpx.Client:
    global _client, _client_key
    cfg = _config()
    if not cfg["base_url"] or not cfg["token"]:
        raise ToolError(
            "OpenProject is not configured: set OPENPROJECT_BASE_URL and OPENPROJECT_API_TOKEN "
            "(hermes mcp configure OpenProjectMCP) and restart Hermes."
        )
    key = (cfg["base_url"], cfg["token"], cfg["timeout"])
    with _client_lock:
        if _client is None or _client_key != key:
            if _client is not None:
                _client.close()
            _client = httpx.Client(
                base_url=f"{cfg['base_url']}/api/v3/",
                auth=("apikey", cfg["token"]),
                headers={"Accept": "application/hal+json, application/json", "User-Agent": _USER_AGENT},
                timeout=cfg["timeout"],
                follow_redirects=True,
            )
            _client_key = key
        return _client


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:300] or f"HTTP {resp.status_code}"
    message = str(body.get("message") or "").strip()
    errors = (body.get("_embedded") or {}).get("errors") or []
    details = [str(e.get("message")) for e in errors if isinstance(e, dict) and e.get("message")]
    if details:
        message = f"{message} ({'; '.join(details)})" if message else "; ".join(details)
    return message or f"HTTP {resp.status_code}"


def _request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    not_found: Optional[str] = None,
) -> Dict[str, Any]:
    """One API call. Reads retry on 429/502/503/504 and network errors; writes
    never retry. Every failure raises a ToolError with a next step."""
    client = _http()
    attempts = 3 if method == "GET" else 1
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = client.request(method, path.lstrip("/"), params=params, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
                continue
            raise ToolError(f"OpenProject did not answer ({type(exc).__name__}: {exc}). Try again in a moment.") from exc
        if resp.status_code in (429, 502, 503, 504) and attempt + 1 < attempts:
            time.sleep(0.5 * (2**attempt))
            continue
        break
    else:  # pragma: no cover - loop always breaks or raises
        raise ToolError(f"OpenProject did not answer: {last_exc}")

    if resp.status_code < 400:
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}
    text = _error_text(resp)
    status = resp.status_code
    if status == 401:
        raise ToolError(f"OpenProject rejected the API token ({text}). Create a new token under My account -> Access tokens.")
    if status == 403:
        raise ToolError(f"Permission denied by OpenProject: {text}. The token's user lacks this permission in that project.")
    if status == 404:
        raise ToolError(not_found or f"Not found in OpenProject: {path}. {text}")
    if status == 409:
        raise ToolError(f"The work package was changed by someone else meanwhile ({text}). Read it again and repeat the change.")
    if status in (400, 422):
        raise ToolError(f"OpenProject rejected the request: {text}")
    raise ToolError(f"OpenProject error HTTP {status}: {text}")


def _elements(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for e in ((payload.get("_embedded") or {}).get("elements") or []) if isinstance(e, dict)]


def _collect(path: str, params: Optional[Dict[str, Any]] = None, *, limit: int = 1000) -> Tuple[List[Dict[str, Any]], int]:
    """All elements of a collection (OpenProject's offset is a 1-based page)."""
    items: List[Dict[str, Any]] = []
    page = 1
    total = 0
    while len(items) < limit:
        query = dict(params or {})
        query.update({"offset": page, "pageSize": _PAGE_SIZE_MAX})
        payload = _request("GET", path, params=query)
        batch = _elements(payload)
        total = int(payload.get("total") or len(batch))
        items.extend(batch)
        if not batch or len(items) >= total:
            break
        page += 1
    return items[:limit], total


def _filters(*entries: Optional[Dict[str, Any]]) -> str:
    return json.dumps([e for e in entries if e], separators=(",", ":"))


def _href_id(link: Any) -> Optional[str]:
    href = link.get("href") if isinstance(link, dict) else None
    if not href:
        return None
    return str(href).rstrip("/").rsplit("/", 1)[-1]


def _title(links: Dict[str, Any], name: str) -> Optional[str]:
    link = links.get(name)
    return link.get("title") if isinstance(link, dict) and link.get("href") else None


# ─── Caches ───────────────────────────────────────────────────────────────────


_cache: Dict[str, Tuple[float, Any]] = {}


def _cached(key: str, loader, ttl: float = _CACHE_TTL_S):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def _me() -> Dict[str, Any]:
    return _cached("me", lambda: _request("GET", "users/me"), ttl=3600)


def _projects() -> List[Dict[str, Any]]:
    return _cached("projects", lambda: _collect("projects")[0])


def _statuses() -> List[Dict[str, Any]]:
    return _cached("statuses", lambda: _elements(_request("GET", "statuses")), ttl=3600)


def _priorities() -> List[Dict[str, Any]]:
    return _cached("priorities", lambda: _elements(_request("GET", "priorities")), ttl=3600)


def _project_types(project_id: str) -> List[Dict[str, Any]]:
    return _cached(f"types:{project_id}", lambda: _elements(_request("GET", f"projects/{project_id}/types")))


def _project_versions(project_id: str) -> List[Dict[str, Any]]:
    return _cached(f"versions:{project_id}", lambda: _collect(f"projects/{project_id}/versions", limit=300)[0])


# ─── Projects and scope ───────────────────────────────────────────────────────


def _project_candidates(project: Dict[str, Any]) -> List[str]:
    return [
        str(project.get("identifier") or "").casefold(),
        str(project.get("name") or "").casefold(),
        str(project.get("id") or ""),
    ]


def _in_scope(project: Dict[str, Any], patterns: Iterable[str]) -> bool:
    patterns = list(patterns)
    if "*" in patterns:
        return True
    candidates = [c for c in _project_candidates(project) if c]
    return any(fnmatch.fnmatchcase(c, p) for p in patterns for c in candidates)


def _readable(project: Dict[str, Any]) -> bool:
    return _in_scope(project, _config()["read"])


def _writable(project: Dict[str, Any]) -> bool:
    cfg = _config()
    return _in_scope(project, cfg["read"]) and _in_scope(project, cfg["write"])


def _project_label(project: Dict[str, Any]) -> str:
    return f"{project.get('identifier')} ({project.get('name')}, id {project.get('id')})"


def _resolve_project(ref: Any, *, write: bool = False) -> Dict[str, Any]:
    """A project by id, identifier or name — exact first, then one unambiguous
    partial match. Anything else raises with the closest candidates."""
    text = str(ref or "").strip()
    if not text:
        raise ToolError("A project is required. Call list_projects to see the identifiers.")
    visible = [p for p in _projects() if _readable(p)]
    folded = text.casefold()
    exact = [p for p in visible if folded in _project_candidates(p)]
    if not exact:
        partial = [p for p in visible if folded in str(p.get("name") or "").casefold() or folded in str(p.get("identifier") or "").casefold()]
        exact = partial if len(partial) == 1 else []
    if len(exact) != 1:
        names = {str(p.get("identifier")): p for p in visible}
        names.update({str(p.get("name")): p for p in visible})
        close = difflib.get_close_matches(text, list(names), n=4, cutoff=0.3)
        seen: List[str] = []
        for name in close:
            label = _project_label(names[name])
            if label not in seen:
                seen.append(label)
        hint = f" Closest matches: {', '.join(seen)}." if seen else ""
        what = "is ambiguous" if len(exact) > 1 else "was not found"
        raise ToolError(f"OpenProject project '{text}' {what}.{hint} Use the identifier from list_projects.")
    project = exact[0]
    if write and not _writable(project):
        raise ToolError(
            f"Project {_project_label(project)} is read-only for this assistant "
            "(OPENPROJECT_WRITE_PROJECTS). Ask the user to allow it, or use a writable project from list_projects."
        )
    return project


def _project_by_id(project_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not project_id:
        return None
    return next((p for p in _projects() if str(p.get("id")) == str(project_id)), None)


def _require_writes() -> None:
    if not writes_enabled():
        raise ToolError("OpenProject is configured read-only (OPENPROJECT_WRITE_PROJECTS is empty).")


# ─── Work packages ────────────────────────────────────────────────────────────


def _user_text(value: Any, limit: int) -> Tuple[Optional[str], bool]:
    raw = value.get("raw") if isinstance(value, dict) else value
    if not raw:
        return None, False
    text = str(raw)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip() + "…"
    return f"<user-content>{text}</user-content>", truncated


def _url(path: str) -> str:
    return f"{_config()['base_url']}/{path.lstrip('/')}"


def _wp_row(wp: Dict[str, Any], *, detail: bool = False) -> Dict[str, Any]:
    links = wp.get("_links") or {}
    project = _project_by_id(_href_id(links.get("project")))
    parent = links.get("parent") if isinstance(links.get("parent"), dict) else {}
    row: Dict[str, Any] = {
        "id": wp.get("id"),
        "display_id": wp.get("displayId") or str(wp.get("id")),
        "subject": wp.get("subject"),
        "type": _title(links, "type"),
        "status": _title(links, "status"),
        "priority": _title(links, "priority"),
        "assignee": _title(links, "assignee"),
        "project": (project or {}).get("identifier") or _title(links, "project"),
        "parent": (parent.get("displayId") or _href_id(parent)) if parent.get("href") else None,
        "start_date": wp.get("startDate") or wp.get("date"),
        "due_date": wp.get("dueDate") or wp.get("date"),
        "updated_at": wp.get("updatedAt"),
    }
    if detail:
        description, truncated = _user_text(wp.get("description"), _DESCRIPTION_LIMIT)
        row.update(
            {
                "description": description,
                "description_truncated": truncated,
                "version": _title(links, "version"),
                "responsible": _title(links, "responsible"),
                "author": _title(links, "author"),
                "estimated_time": wp.get("estimatedTime"),
                "spent_time": wp.get("spentTime"),
                "percentage_done": wp.get("percentageDone"),
                "created_at": wp.get("createdAt"),
                "lock_version": wp.get("lockVersion"),
                "url": _url(f"work_packages/{wp.get('id')}"),
            }
        )
    return {k: v for k, v in row.items() if v not in (None, "")}


def _get_wp(ref: Any) -> Dict[str, Any]:
    text = str(ref or "").strip().lstrip("#")
    if not text or text in (".", "..") or "/" in text:
        raise ToolError("A work package id is required: the display id (e.g. 'AIS-408') or the numeric id.")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", text):
        text = text.upper()  # display ids are case-sensitive; project identifiers are upper case
    wp = _request(
        "GET",
        f"work_packages/{text}",
        not_found=(
            f"Work package '{text}' was not found or is not visible to this token. Display ids are "
            "case-sensitive ('AIS-408'); search_work_packages finds it by subject."
        ),
    )
    project = _project_by_id(_href_id((wp.get("_links") or {}).get("project")))
    if project and not _readable(project):
        raise ToolError(f"Work package '{text}' belongs to {_project_label(project)}, which is outside OPENPROJECT_READ_PROJECTS.")
    return wp


def _wp_project(wp: Dict[str, Any]) -> Dict[str, Any]:
    project = _project_by_id(_href_id((wp.get("_links") or {}).get("project")))
    if not project:
        raise ToolError("The work package's project is not visible to this token.")
    return project


def _pick_named(items: List[Dict[str, Any]], name: Any, what: str, *, label: str = "name") -> Dict[str, Any]:
    text = str(name or "").strip()
    folded = text.casefold()
    for item in items:
        if str(item.get("id")) == text or str(item.get(label) or "").casefold() == folded:
            return item
    partial = [i for i in items if folded and folded in str(i.get(label) or "").casefold()]
    if len(partial) == 1:
        return partial[0]
    allowed = ", ".join(str(i.get(label)) for i in items) or "none"
    raise ToolError(f"Unknown {what} '{text}'. Allowed: {allowed}.")


def _schema_allowed(form: Dict[str, Any], field: str) -> List[Dict[str, Any]]:
    """Allowed values of a form schema field, following the link when needed."""
    spec = ((form.get("_embedded") or {}).get("schema") or {}).get(field) or {}
    embedded = (spec.get("_embedded") or {}).get("allowedValues")
    if isinstance(embedded, list):
        return [v for v in embedded if isinstance(v, dict)]
    link = (spec.get("_links") or {}).get("allowedValues")
    if isinstance(link, dict) and link.get("href"):
        href = str(link["href"]).split("/api/v3/", 1)[-1]
        return _collect(href, limit=500)[0]
    if isinstance(link, list):
        return [{"id": _href_id(v), "name": v.get("title"), "_links": {"self": v}} for v in link if isinstance(v, dict)]
    return []


def _validation_errors(form: Dict[str, Any]) -> Dict[str, str]:
    errors = (form.get("_embedded") or {}).get("validationErrors") or {}
    out: Dict[str, str] = {}
    for field, err in errors.items():
        if isinstance(err, dict):
            nested = (err.get("_embedded") or {}).get("errors") or []
            out[field] = "; ".join(str(e.get("message")) for e in nested if isinstance(e, dict)) or str(err.get("message") or err)
        else:
            out[field] = str(err)
    return out


def _link(kind: str, ident: Any) -> Dict[str, Optional[str]]:
    return {"href": f"/api/v3/{kind}/{ident}"} if ident is not None else {"href": None}


def _resolve_user(value: Any, allowed: List[Dict[str, Any]]) -> Optional[str]:
    text = str(value or "").strip()
    if text.casefold() in ("none", "null", "unassigned", "-"):
        return None
    if text.casefold() == "me":
        return str(_me().get("id"))
    return str(_pick_named(allowed, text, "assignee").get("id"))


def _resolve_parent(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if text.casefold() in ("none", "null", "-"):
        return None
    return str(_get_wp(text).get("id"))


def _hours_iso(value: Any) -> str:
    """1.5 / "1,5" / "1h30m" / "90m" / "PT1H30M" -> "PT1H30M"."""
    text = str(value or "").strip()
    if not text:
        raise ToolError("hours is required, e.g. 1.5 or '1h30m'.")
    if re.fullmatch(r"P(T(\d+(\.\d+)?H)?(\d+(\.\d+)?M)?)?", text.upper()) and text.upper() not in ("P", "PT"):
        return text.upper()
    match = re.fullmatch(r"\s*(?:(\d+(?:[.,]\d+)?)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*", text.lower())
    if match and (match.group(1) or match.group(2)):
        minutes = round(float((match.group(1) or "0").replace(",", ".")) * 60) + int(match.group(2) or 0)
    else:
        try:
            minutes = round(float(text.replace(",", ".")) * 60)
        except ValueError as exc:
            raise ToolError(f"Cannot read hours '{text}'. Use decimal hours (1.5) or '1h30m'.") from exc
    if minutes <= 0:
        raise ToolError("hours must be greater than zero.")
    h, m = divmod(minutes, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "")


def _iso_to_hours(value: Any) -> float:
    match = re.fullmatch(r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?", str(value or ""))
    if not match:
        return 0.0
    d, h, m, s = (float(g) if g else 0.0 for g in match.groups())
    return round(d * 24 + h + m / 60 + s / 3600, 4)


def _day(value: Any, what: str) -> str:
    text = str(value or "").strip()[:10]
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ToolError(f"{what} must be an ISO date (YYYY-MM-DD), got '{value}'.") from exc


def _preview(action: str, summary: Dict[str, Any], errors: Dict[str, str]) -> Dict[str, Any]:
    ready = not errors
    return {
        "state": "preview",
        "action": action,
        "ready": ready,
        "would": summary,
        "validation_errors": errors,
        "message": (
            "Validated by OpenProject. Show this to the user and call again with confirm=true to execute."
            if ready
            else "OpenProject would reject this: fix the validation_errors first. Nothing was changed."
        ),
    }


def _reject_if_invalid(errors: Dict[str, str]) -> None:
    if errors:
        raise ToolError("OpenProject rejected the change: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))


def _recent_duplicate(project: Dict[str, Any], subject: str) -> Optional[Dict[str, Any]]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=_DUPLICATE_WINDOW_S)
    payload = _request(
        "GET",
        "work_packages",
        params={
            "filters": _filters(
                {"project_id": {"operator": "=", "values": [str(project["id"])]}},
                {"subject_or_id": {"operator": "**", "values": [subject]}},
            ),
            "sortBy": json.dumps([["created_at", "desc"]]),
            "pageSize": 10,
        },
    )
    for wp in _elements(payload):
        if str(wp.get("subject") or "").strip().casefold() != subject.strip().casefold():
            continue
        try:
            created = dt.datetime.fromisoformat(str(wp.get("createdAt")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if created >= since:
            return wp
    return None


# ─── Read tools ───────────────────────────────────────────────────────────────


@mcp.tool()
def list_projects(search: Optional[str] = None, writable_only: bool = False) -> Dict[str, Any]:
    """Projects this assistant can read, with `writable` per project and the current user.

    Use the `identifier` (e.g. 'AIS') wherever a tool asks for a project.
    """
    folded = str(search or "").strip().casefold()
    rows = []
    for p in _projects():
        if not _readable(p):
            continue
        if folded and folded not in str(p.get("name") or "").casefold() and folded not in str(p.get("identifier") or "").casefold():
            continue
        writable = _writable(p)
        if writable_only and not writable:
            continue
        rows.append({"identifier": p.get("identifier"), "name": p.get("name"), "id": p.get("id"), "writable": writable})
    me = _me()
    return {"current_user": {"id": me.get("id"), "name": me.get("name")}, "count": len(rows), "projects": rows}


def _time_activities(project_id: str) -> List[str]:
    """Time-tracking activities of a project. The project-only form needs a
    permission many users lack (403); the form for one of its work packages
    answers for everyone who may log time there."""
    bodies = [{"_links": {"project": _link("projects", project_id)}}]
    sample = _elements(
        _request(
            "GET",
            "work_packages",
            params={"filters": _filters({"project_id": {"operator": "=", "values": [project_id]}}), "pageSize": 1},
        )
    )
    if sample:
        bodies.insert(0, {"_links": {"entity": _link("work_packages", sample[0].get("id"))}})
    for body in bodies:
        try:
            names = [a.get("name") for a in _schema_allowed(_request("POST", "time_entries/form", body=body), "activity") if a.get("name")]
        except ToolError:
            continue
        if names:
            return names
    return []


@mcp.tool()
def project_context(project: str) -> Dict[str, Any]:
    """Everything needed to fill a work package or time entry in one call.

    Returns the project's types, all statuses, priorities, open versions,
    assignable members and time-tracking activities.
    """
    proj = _resolve_project(project)
    pid = str(proj["id"])
    form = _request("POST", f"projects/{pid}/work_packages/form", body={})
    members = [u.get("name") for u in _schema_allowed(form, "assignee") if u.get("name")]
    activities = _time_activities(pid)
    versions = [v.get("name") for v in _project_versions(pid) if v.get("status") != "closed"]
    return {
        "project": {"identifier": proj.get("identifier"), "name": proj.get("name"), "id": proj.get("id"), "writable": _writable(proj)},
        "types": [t.get("name") for t in _project_types(pid)],
        "statuses": [{"name": s.get("name"), "closed": bool(s.get("isClosed"))} for s in _statuses()],
        "priorities": [p.get("name") for p in _priorities()],
        "versions": versions,
        "assignable_members": members,
        "time_activities": activities,
        "note": "Status changes must follow the project's workflow; update_work_package names the allowed next statuses when a change is rejected.",
    }


@mcp.tool()
def search_work_packages(
    project: Optional[str] = None,
    text: Optional[str] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    type: Optional[str] = None,
    updated_since: Optional[str] = None,
    sort: str = "updated_at desc",
    page: int = 1,
    page_size: int = 25,
) -> Dict[str, Any]:
    """Find work packages. All filters combine; without `project` every readable project is searched.

    - text: matches subject and id/display id
    - status: 'open', 'closed' or a status name
    - assignee: 'me', a user id or a member name
    - type: 'Task', 'Bug', ... ; updated_since: YYYY-MM-DD
    - sort: '<field> asc|desc' (updated_at, created_at, id, due_date, priority, status)
    Use get_work_package for the description and comments.
    """
    filters: List[Dict[str, Any]] = []
    proj = _resolve_project(project) if project else None
    if proj:
        filters.append({"project_id": {"operator": "=", "values": [str(proj["id"])]}})
    elif "*" not in _config()["read"]:
        ids = [str(p["id"]) for p in _projects() if _readable(p)]
        if not ids:
            raise ToolError("No project is readable under OPENPROJECT_READ_PROJECTS.")
        filters.append({"project_id": {"operator": "=", "values": ids}})
    if text:
        filters.append({"subject_or_id": {"operator": "**", "values": [str(text)]}})
    if status:
        folded = str(status).strip().casefold()
        if folded in ("open", "closed"):
            filters.append({"status_id": {"operator": "o" if folded == "open" else "c", "values": []}})
        else:
            filters.append({"status_id": {"operator": "=", "values": [str(_pick_named(_statuses(), status, "status")["id"])]}})
    if assignee:
        folded = str(assignee).strip().casefold()
        if folded == "me":
            uid = str(_me().get("id"))
        elif str(assignee).strip().isdigit():
            uid = str(assignee).strip()
        else:
            principals = _elements(
                _request("GET", "principals", params={"filters": _filters({"name": {"operator": "~", "values": [str(assignee)]}}), "pageSize": 20})
            )
            uid = str(_pick_named(principals, assignee, "assignee")["id"])
        filters.append({"assigned_to_id": {"operator": "=", "values": [uid]}})
    if type:
        types = _project_types(str(proj["id"])) if proj else _cached("types", lambda: _elements(_request("GET", "types")), ttl=3600)
        filters.append({"type_id": {"operator": "=", "values": [str(_pick_named(types, type, "type")["id"])]}})
    if updated_since:
        filters.append({"updated_at": {"operator": ">=", "values": [_day(updated_since, "updated_since")]}})
    field, _, direction = str(sort or "updated_at desc").partition(" ")
    field = {"updated": "updated_at", "created": "created_at"}.get(field, field)
    if field not in ("updated_at", "created_at", "id", "due_date", "start_date", "priority", "status", "subject", "type"):
        raise ToolError("sort field must be one of updated_at, created_at, id, due_date, start_date, priority, status, subject, type.")
    size = max(1, min(int(page_size or 25), _PAGE_SIZE_MAX))
    payload = _request(
        "GET",
        "work_packages",
        params={
            "filters": _filters(*filters),
            "sortBy": json.dumps([[field, "asc" if direction.strip().lower() == "asc" else "desc"]]),
            "offset": max(1, int(page or 1)),
            "pageSize": size,
        },
    )
    total = int(payload.get("total") or 0)
    items = [_wp_row(wp) for wp in _elements(payload)]
    for item in items:
        if isinstance(item.get("subject"), str) and len(item["subject"]) > _LIST_TEXT_LIMIT:
            item["subject"] = item["subject"][:_LIST_TEXT_LIMIT] + "…"
    pages = max(1, -(-total // size))
    result: Dict[str, Any] = {"total": total, "page": max(1, int(page or 1)), "pages": pages, "work_packages": items}
    if total > size:
        result["hint"] = "More results exist: narrow the filters rather than paging through everything."
    return result


@mcp.tool()
def get_work_package(work_package: str, include: Optional[List[str]] = None) -> Dict[str, Any]:
    """One work package by display id ('AIS-408') or numeric id, with its description.

    include: any of 'relations', 'comments', 'children' (default: relations).
    """
    wp = _get_wp(work_package)
    row = _wp_row(wp, detail=True)
    wanted = {str(i).strip().lower() for i in (include if include is not None else ["relations"])}
    wid = str(wp.get("id"))
    if "relations" in wanted:
        rels = _elements(
            _request("GET", "relations", params={"filters": _filters({"involved": {"operator": "=", "values": [wid]}}), "pageSize": 100})
        )
        row["relations"] = [
            {
                "relation_id": r.get("id"),
                "type": r.get("type"),
                "from": _title(r.get("_links") or {}, "from"),
                "to": _title(r.get("_links") or {}, "to"),
                "from_id": _href_id((r.get("_links") or {}).get("from")),
                "to_id": _href_id((r.get("_links") or {}).get("to")),
            }
            for r in rels
        ]
    if "children" in wanted:
        children = ((wp.get("_links") or {}).get("children")) or []
        row["children"] = [{"id": _href_id(c), "subject": c.get("title")} for c in children if isinstance(c, dict)]
    if "comments" in wanted:
        activities = _elements(_request("GET", f"work_packages/{wid}/activities"))
        comments = []
        for a in activities:
            text, _ = _user_text(a.get("comment"), _COMMENT_LIMIT)
            if text:
                comments.append({"author": _title(a.get("_links") or {}, "user"), "at": a.get("createdAt"), "comment": text})
        row["comments"] = comments[-20:]
    return row


# ─── Time entries ─────────────────────────────────────────────────────────────


def _months(start: str, end: str) -> List[str]:
    cur = dt.date.fromisoformat(start).replace(day=1)
    last = dt.date.fromisoformat(end)
    out = []
    while cur <= last:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _display_ids(wp_ids: Iterable[str]) -> Dict[str, str]:
    ids = sorted({i for i in wp_ids if i})
    out: Dict[str, str] = {}
    for start in range(0, len(ids), _PAGE_SIZE_MAX):
        chunk = ids[start : start + _PAGE_SIZE_MAX]
        payload = _request(
            "GET",
            "work_packages",
            params={"filters": _filters({"id": {"operator": "=", "values": chunk}}), "pageSize": _PAGE_SIZE_MAX},
        )
        for wp in _elements(payload):
            out[str(wp.get("id"))] = str(wp.get("displayId") or wp.get("id"))
    return out


def _time_entry_row(entry: Dict[str, Any], display: Dict[str, str]) -> Dict[str, Any]:
    links = entry.get("_links") or {}
    target = links.get("entity") if isinstance(links.get("entity"), dict) and links["entity"].get("href") else links.get("workPackage")
    wp_id = _href_id(target) if isinstance(target, dict) and "work_packages" in str(target.get("href") or "") else None
    project = _project_by_id(_href_id(links.get("project")))
    hours = _iso_to_hours(entry.get("hours"))
    comment = (entry.get("comment") or {}).get("raw") if isinstance(entry.get("comment"), dict) else entry.get("comment")
    return {
        "id": entry.get("id"),
        "spent_on": entry.get("spentOn"),
        "hours": hours,
        "duration_seconds": int(round(hours * 3600)),
        "work_package_id": display.get(str(wp_id), wp_id) if wp_id else None,
        "work_package_subject": target.get("title") if isinstance(target, dict) else None,
        "project": (project or {}).get("identifier") or _title(links, "project"),
        "user": _title(links, "user"),
        "category": _title(links, "activity"),
        "comment": comment or "",
        "updated_at": entry.get("updatedAt"),
    }


@mcp.tool()
def list_time_entries(
    date_from: str,
    date_to: str,
    user: str = "me",
    project: Optional[str] = None,
    work_package: Optional[str] = None,
) -> Dict[str, Any]:
    """Booked time (time entries) in a date range, complete, with hours per entry.

    - date_from / date_to: YYYY-MM-DD, inclusive
    - user: 'me' (default), 'all' or a user id
    Every entry is returned (no paging); `complete` says whether the range was
    fetched in full and `months[]` gives the per-month count.
    """
    start, end = _day(date_from, "date_from"), _day(date_to, "date_to")
    if start > end:
        raise ToolError("date_from must not be after date_to.")
    filters: List[Dict[str, Any]] = [{"spent_on": {"operator": "<>d", "values": [start, end]}}]
    who = str(user or "me").strip()
    if who.casefold() != "all":
        filters.append({"user_id": {"operator": "=", "values": ["me" if who.casefold() == "me" else who]}})
    if project:
        filters.append({"project_id": {"operator": "=", "values": [str(_resolve_project(project)["id"])]}})
    if work_package:
        filters.append({"work_package_id": {"operator": "=", "values": [str(_get_wp(work_package)["id"])]}})
    entries, total = _collect(
        "time_entries",
        {"filters": _filters(*filters), "sortBy": json.dumps([["spent_on", "asc"]])},
        limit=_TIME_ENTRY_LIMIT,
    )
    complete = len(entries) >= total
    entries = [e for e in entries if (p := _project_by_id(_href_id((e.get("_links") or {}).get("project")))) is None or _readable(p)]
    wp_ids = []
    for e in entries:
        links = e.get("_links") or {}
        target = links.get("entity") if isinstance(links.get("entity"), dict) and links["entity"].get("href") else links.get("workPackage")
        if isinstance(target, dict) and "work_packages" in str(target.get("href") or ""):
            wp_ids.append(_href_id(target))
    display = _display_ids(wp_ids)
    rows = [_time_entry_row(e, display) for e in entries]
    per_month: Dict[str, int] = {m: 0 for m in _months(start, end)}
    for row in rows:
        month = str(row.get("spent_on") or "")[:7]
        if month in per_month:
            per_month[month] += 1
    result: Dict[str, Any] = {
        "window": {"start": start, "end": end},
        "count": len(rows),
        "total_hours": round(sum(r["hours"] for r in rows), 2),
        "complete": bool(complete),
        "months": [{"month": m, "count": c, "complete": bool(complete)} for m, c in per_month.items()],
        "time_entries": rows,
    }
    if not complete:
        result["hint"] = f"Only the first {_TIME_ENTRY_LIMIT} of {total} entries were returned; narrow the range."
    return result


# ─── Write tools ──────────────────────────────────────────────────────────────


def create_work_package(
    project: str,
    subject: str,
    type: str = "Task",
    description: Optional[str] = None,
    parent: Optional[str] = None,
    assignee: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    version: Optional[str] = None,
    start_date: Optional[str] = None,
    due_date: Optional[str] = None,
    estimated_hours: Optional[str] = None,
    confirm: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Create a work package (preview first, confirm=true to create).

    - project: identifier from list_projects; type: 'Task', 'Bug', ...
    - description: Markdown (code blocks allowed)
    - parent: display id ('AIS-3') or numeric id; assignee: 'me', id or member name
    The same subject created in this project within the last 15 minutes is
    returned instead of a duplicate unless force=true.
    """
    _require_writes()
    proj = _resolve_project(project, write=True)
    pid = str(proj["id"])
    if not str(subject or "").strip():
        raise ToolError("subject is required.")
    links: Dict[str, Any] = {"type": _link("types", _pick_named(_project_types(pid), type, "type")["id"])}
    if parent:
        links["parent"] = _link("work_packages", _resolve_parent(parent))
    if priority:
        links["priority"] = _link("priorities", _pick_named(_priorities(), priority, "priority")["id"])
    if status:
        links["status"] = _link("statuses", _pick_named(_statuses(), status, "status")["id"])
    if version:
        links["version"] = _link("versions", _pick_named(_project_versions(pid), version, "version")["id"])
    draft: Dict[str, Any] = {"subject": str(subject).strip(), "_links": links}
    if description:
        draft["description"] = {"format": "markdown", "raw": str(description)}
    if start_date:
        draft["startDate"] = _day(start_date, "start_date")
    if due_date:
        draft["dueDate"] = _day(due_date, "due_date")
    if estimated_hours:
        draft["estimatedTime"] = _hours_iso(estimated_hours)
    form = _request("POST", f"projects/{pid}/work_packages/form", body=draft)
    if assignee:
        links["assignee"] = _link("users", _resolve_user(assignee, _schema_allowed(form, "assignee")))
        form = _request("POST", f"projects/{pid}/work_packages/form", body=draft)
    errors = _validation_errors(form)
    summary = {
        "project": proj.get("identifier"),
        "subject": draft["subject"],
        "type": type,
        "parent": parent,
        "assignee": assignee,
        "status": status,
        "description_chars": len(description or ""),
    }
    if not force:
        duplicate = _recent_duplicate(proj, draft["subject"])
        if duplicate:
            return {
                "state": "duplicate",
                "message": "This work package was already created a few minutes ago — nothing new was created. Use it, or pass force=true for a second one.",
                "work_package": _wp_row(duplicate),
            }
    if not confirm:
        return _preview("create_work_package", summary, errors)
    _reject_if_invalid(errors)
    payload = (form.get("_embedded") or {}).get("payload") or draft
    created = _request("POST", "work_packages", body=payload)
    return {"state": "created", "work_package": _wp_row(created, detail=True)}


def update_work_package(
    work_package: str,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    priority: Optional[str] = None,
    assignee: Optional[str] = None,
    parent: Optional[str] = None,
    project: Optional[str] = None,
    version: Optional[str] = None,
    start_date: Optional[str] = None,
    due_date: Optional[str] = None,
    estimated_hours: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Change a work package (preview first, confirm=true to save).

    - project: MOVE it to another project (identifier)
    - status: name; one workflow step at a time, rejected changes name the allowed next statuses
    - assignee / parent: 'none' clears them; parent takes a display id ('AIS-3')
    - description: Markdown, replaces the whole description
    """
    _require_writes()
    wp = _get_wp(work_package)
    source = _wp_project(wp)
    if not _writable(source):
        raise ToolError(f"{_project_label(source)} is read-only for this assistant (OPENPROJECT_WRITE_PROJECTS).")
    wid = str(wp.get("id"))
    target = _resolve_project(project, write=True) if project else source
    draft: Dict[str, Any] = {"lockVersion": wp.get("lockVersion"), "_links": {}}
    links = draft["_links"]
    changes: Dict[str, Any] = {}
    if project and str(target["id"]) != str(source["id"]):
        links["project"] = _link("projects", target["id"])
        changes["project"] = f"{source.get('identifier')} -> {target.get('identifier')}"
    if subject is not None:
        draft["subject"] = str(subject).strip()
        changes["subject"] = draft["subject"]
    if description is not None:
        draft["description"] = {"format": "markdown", "raw": str(description)}
        changes["description_chars"] = len(description)
    if type:
        links["type"] = _link("types", _pick_named(_project_types(str(target["id"])), type, "type")["id"])
        changes["type"] = type
    if priority:
        links["priority"] = _link("priorities", _pick_named(_priorities(), priority, "priority")["id"])
        changes["priority"] = priority
    if version:
        links["version"] = _link("versions", _pick_named(_project_versions(str(target["id"])), version, "version")["id"])
        changes["version"] = version
    if parent:
        links["parent"] = _link("work_packages", _resolve_parent(parent))
        changes["parent"] = parent
    if start_date:
        draft["startDate"] = _day(start_date, "start_date")
        changes["start_date"] = draft["startDate"]
    if due_date:
        draft["dueDate"] = _day(due_date, "due_date")
        changes["due_date"] = draft["dueDate"]
    if estimated_hours:
        draft["estimatedTime"] = _hours_iso(estimated_hours)
        changes["estimated_time"] = draft["estimatedTime"]
    form = _request("POST", f"work_packages/{wid}/form", body=draft)
    if status:
        allowed = _schema_allowed(form, "status")
        current = _title(wp.get("_links") or {}, "status")
        try:
            chosen = _pick_named(allowed or _statuses(), status, "status")
        except ToolError:
            names = ", ".join(str(s.get("name")) for s in allowed if s.get("name") != current) or "none"
            raise ToolError(
                f"Status '{status}' is not reachable from '{current}' in this workflow. Allowed next: {names}. "
                "Change it one step at a time."
            ) from None
        links["status"] = _link("statuses", chosen["id"])
        changes["status"] = f"{current} -> {chosen.get('name')}"
    if assignee:
        links["assignee"] = _link("users", _resolve_user(assignee, _schema_allowed(form, "assignee")))
        changes["assignee"] = assignee
    if not changes:
        raise ToolError("Nothing to change: pass at least one field.")
    if status or assignee:
        form = _request("POST", f"work_packages/{wid}/form", body=draft)
    errors = _validation_errors(form)
    if not confirm:
        return _preview("update_work_package", {"work_package": wp.get("displayId") or wid, "changes": changes}, errors)
    _reject_if_invalid(errors)
    payload = dict((form.get("_embedded") or {}).get("payload") or {})
    payload = payload or draft
    payload["lockVersion"] = wp.get("lockVersion")
    if "project" in links:
        payload.setdefault("_links", {})["project"] = links["project"]
    updated = _request("PATCH", f"work_packages/{wid}", body=payload)
    if "project" in links and _href_id((updated.get("_links") or {}).get("project")) != str(target["id"]):
        raise ToolError(
            "OpenProject saved the other changes but did not move the work package; the user needs the "
            "'Move work packages' permission in both projects."
        )
    return {"state": "updated", "changes": changes, "work_package": _wp_row(updated, detail=True)}


def delete_work_package(work_package: str, confirm: bool = False) -> Dict[str, Any]:
    """Delete a work package, including its children and time entries (preview first, confirm=true to delete)."""
    _require_writes()
    wp = _get_wp(work_package)
    project = _wp_project(wp)
    if not _writable(project):
        raise ToolError(f"{_project_label(project)} is read-only for this assistant (OPENPROJECT_WRITE_PROJECTS).")
    row = _wp_row(wp)
    if not confirm:
        children = len(((wp.get("_links") or {}).get("children")) or [])
        return _preview("delete_work_package", {"work_package": row, "children_deleted_too": children}, {})
    _request("DELETE", f"work_packages/{wp.get('id')}")
    return {"state": "deleted", "work_package": row}


def add_comment(work_package: str, comment: str, confirm: bool = False) -> Dict[str, Any]:
    """Add a Markdown comment to a work package (preview first, confirm=true to post)."""
    _require_writes()
    wp = _get_wp(work_package)
    project = _wp_project(wp)
    if not _writable(project):
        raise ToolError(f"{_project_label(project)} is read-only for this assistant (OPENPROJECT_WRITE_PROJECTS).")
    if not str(comment or "").strip():
        raise ToolError("comment is required.")
    if not confirm:
        return _preview("add_comment", {"work_package": wp.get("displayId"), "comment_chars": len(comment)}, {})
    _request("POST", f"work_packages/{wp.get('id')}/activities", body={"comment": {"raw": str(comment)}})
    return {"state": "commented", "work_package": wp.get("displayId") or wp.get("id")}


_RELATION_TYPES = ("relates", "duplicates", "duplicated", "blocks", "blocked", "precedes", "follows", "includes", "partof", "requires", "required")


def manage_relation(
    action: str,
    work_package: Optional[str] = None,
    related_to: Optional[str] = None,
    type: str = "relates",
    relation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Create or delete a relation between two work packages (preview first, confirm=true to execute).

    - action 'create': work_package, related_to, type (relates, blocks, blocked, precedes, follows, duplicates, includes, partof, requires)
    - action 'delete': relation_id (from get_work_package relations)
    """
    _require_writes()
    verb = str(action or "").strip().lower()
    if verb == "create":
        if not work_package or not related_to:
            raise ToolError("create needs work_package and related_to.")
        kind = str(type or "relates").strip().lower()
        if kind not in _RELATION_TYPES:
            raise ToolError(f"Unknown relation type '{type}'. Allowed: {', '.join(_RELATION_TYPES)}.")
        source, target = _get_wp(work_package), _get_wp(related_to)
        if not _writable(_wp_project(source)):
            raise ToolError("The source work package's project is read-only for this assistant.")
        summary = {"from": source.get("displayId"), "type": kind, "to": target.get("displayId")}
        if not confirm:
            return _preview("create_relation", summary, {})
        rel = _request(
            "POST",
            f"work_packages/{source.get('id')}/relations",
            body={"type": kind, "_links": {"to": _link("work_packages", target.get("id"))}},
        )
        return {"state": "created", "relation_id": rel.get("id"), **summary}
    if verb == "delete":
        if not relation_id:
            raise ToolError("delete needs relation_id (see get_work_package relations).")
        rel = _request("GET", f"relations/{relation_id}", not_found=f"Relation {relation_id} was not found.")
        summary = {"relation_id": relation_id, "type": rel.get("type"), "from": _title(rel.get("_links") or {}, "from"), "to": _title(rel.get("_links") or {}, "to")}
        if not confirm:
            return _preview("delete_relation", summary, {})
        _request("DELETE", f"relations/{relation_id}")
        return {"state": "deleted", **summary}
    raise ToolError("action must be 'create' or 'delete'.")


def log_time(
    action: str = "create",
    work_package: Optional[str] = None,
    spent_on: Optional[str] = None,
    hours: Optional[str] = None,
    activity: Optional[str] = None,
    comment: Optional[str] = None,
    time_entry_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Book, change or delete time on a work package (preview first, confirm=true to save).

    - create: work_package, spent_on (YYYY-MM-DD, default today), hours (1.5 or '1h30m'), activity (project_context lists them), comment
    - update: time_entry_id plus the fields to change
    - delete: time_entry_id
    The saved entry comes back in `time_entries`, same shape as list_time_entries.
    """
    _require_writes()
    verb = str(action or "create").strip().lower()
    if verb == "delete":
        if not time_entry_id:
            raise ToolError("delete needs time_entry_id (from list_time_entries).")
        entry = _request("GET", f"time_entries/{time_entry_id}", not_found=f"Time entry {time_entry_id} was not found.")
        row = _time_entry_row(entry, {})
        if not confirm:
            return _preview("delete_time_entry", row, {})
        _request("DELETE", f"time_entries/{time_entry_id}")
        return {"state": "deleted", "deleted_time_entry_id": entry.get("id"), "time_entry": row}

    if verb not in ("create", "update"):
        raise ToolError("action must be 'create', 'update' or 'delete'.")
    existing = None
    body: Dict[str, Any] = {"_links": {}}
    if verb == "update":
        if not time_entry_id:
            raise ToolError("update needs time_entry_id (from list_time_entries).")
        existing = _request("GET", f"time_entries/{time_entry_id}", not_found=f"Time entry {time_entry_id} was not found.")
    wp = _get_wp(work_package) if work_package else None
    if verb == "create" and not wp:
        raise ToolError("create needs work_package (display id like 'AIS-408').")
    if wp:
        project = _wp_project(wp)
        if not _writable(project):
            raise ToolError(f"{_project_label(project)} is read-only for this assistant (OPENPROJECT_WRITE_PROJECTS).")
        body["_links"]["entity"] = _link("work_packages", wp.get("id"))
    if spent_on or verb == "create":
        body["spentOn"] = _day(spent_on or dt.date.today().isoformat(), "spent_on")
    if hours is not None or verb == "create":
        body["hours"] = _hours_iso(hours)
    if comment is not None:
        body["comment"] = {"format": "plain", "raw": str(comment)}
    form_path = "time_entries/form" if verb == "create" else f"time_entries/{time_entry_id}/form"
    form = _request("POST", form_path, body=body)
    if activity:
        chosen = _pick_named(_schema_allowed(form, "activity"), activity, "activity")
        activity = chosen.get("name") or activity
        body["_links"]["activity"] = _link("time_entries/activities", chosen["id"])
        form = _request("POST", form_path, body=body)
    errors = _validation_errors(form)
    summary = {
        "work_package": (wp or {}).get("displayId"),
        "spent_on": body.get("spentOn"),
        "hours": _iso_to_hours(body.get("hours")) if body.get("hours") else None,
        "activity": activity,
        "comment": comment,
        "time_entry_id": time_entry_id,
    }
    if not confirm:
        return _preview(f"{verb}_time_entry", {k: v for k, v in summary.items() if v is not None}, errors)
    _reject_if_invalid(errors)
    payload = (form.get("_embedded") or {}).get("payload") or body
    saved = (
        _request("POST", "time_entries", body=payload)
        if verb == "create"
        else _request("PATCH", f"time_entries/{time_entry_id}", body=payload)
    )
    display = {str(wp.get("id")): str(wp.get("displayId"))} if wp else {}
    row = _time_entry_row(saved, display)
    return {"state": "created" if verb == "create" else "updated", "time_entry": row, "time_entries": [row]}


WRITE_TOOLS = (create_work_package, update_work_package, delete_work_package, add_comment, manage_relation, log_time)


def register_write_tools() -> None:
    """Write tools exist only when OPENPROJECT_WRITE_PROJECTS allows writes,
    so a read-only setup never offers them to the model (AIS-330)."""
    for fn in WRITE_TOOLS:
        mcp.tool()(fn)


if writes_enabled():
    register_write_tools()


if __name__ == "__main__":
    mcp.run()
