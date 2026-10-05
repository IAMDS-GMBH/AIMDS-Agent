"""Prefer the Suite's OpenProject tools over the bundled local server (AIS-479).

The bundled ``OpenProjectMCP`` and the Suite's ``go-mcp-openproject`` expose
the same ``pm_*`` tool contract. Per OpenProject domain the model gets exactly
one of them:

* Suite tools registered, the account linked there and the Suite pointing at
  the same OpenProject domain as a local server → that local server's tools
  are hidden; writes then run as the user's own Suite-linked account.
* Suite not linked yet but a local server for the same domain exists → the
  Suite's ``pm_*`` tools are hidden (the local server keeps working) and the
  account is linked automatically with the locally configured token: the
  one-time ``link_url`` from ``pm_link_status`` is redeemed through the link
  page's own endpoints (``api/info`` tells the instance without consuming the
  link, ``api/submit`` stores the token).
* Different domains, Suite down, stale check → both stay as they are; a
  duplicate tool set is the safe failure, a missing one is not.
* Once the Suite serves a local server's domain for the linked account, that
  local server is uninstalled (config entry, its credentials) and the user
  gets a one-time notice (AIS-483) — the Suite now holds the token.

Everything here is deterministic backend code on the desktop ticker. The model
is never asked to do any of it; it only sees the resulting tool set.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

STATE_FILE = "openproject_suite.json"
#: Hiding is only trusted while the decision is fresh: a stopped desktop (or a
#: CLI-only machine) must not keep the local tools hidden for good.
STATE_TTL_SECONDS = 2 * 3600
#: pm_link_status on a linked account is cheap and side-effect free.
LINKED_RECHECK_SECONDS = 10 * 60
#: On an unlinked account every pm_link_status creates a one-time link, and the
#: Suite allows 10 per user and hour — stay far below that.
UNLINKED_RECHECK_SECONDS = 30 * 60
NO_SUITE_RECHECK_SECONDS = 5 * 60
LINK_BACKOFF_BASE_SECONDS = 30 * 60
LINK_BACKOFF_MAX_SECONDS = 6 * 3600
HTTP_TIMEOUT_SECONDS = 15.0
STATE_RELOAD_SECONDS = 5.0

_LOCAL_SERVER_MARKER = "OpenProjectMCP"
_SUITE_SERVER = "AIMDSSuiteMCP"
_PM_TOOL_RE = re.compile(r"(?:^|[-_])pm_[a-z_]+$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_run_lock = threading.Lock()
_state_lock = threading.Lock()
_cached_state: Dict[str, Any] = {}
_cached_at = 0.0
_cached_mtime: Optional[float] = None


# --------------------------------------------------------------------------- helpers


def enabled(config: Optional[dict] = None) -> bool:
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        section = config.get("openproject") or {}
        return bool(section.get("prefer_suite", True)) if isinstance(section, dict) else True
    except Exception:
        return True


def remove_local_enabled(config: Optional[dict] = None) -> bool:
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        section = config.get("openproject") or {}
        return bool(section.get("remove_local", True)) if isinstance(section, dict) else True
    except Exception:
        return True


def auto_link_enabled(config: Optional[dict] = None) -> bool:
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        section = config.get("openproject") or {}
        return bool(section.get("auto_link", True)) if isinstance(section, dict) else True
    except Exception:
        return True


def instance_key(url: Any) -> str:
    """``https://OP.example.com:443/api/v3/`` → ``https://op.example.com``.

    Scheme + host + non-default port: what decides "the same OpenProject"."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    scheme = (parts.scheme or "https").lower()
    port = parts.port
    default = {"https": 443, "http": 80}.get(scheme)
    netloc = host if port in (None, default) else f"{host}:{port}"
    return f"{scheme}://{netloc}"


def is_pm_tool(mcp_tool_name: str) -> bool:
    """``pm_list_projects`` / ``mcp_openproject-pm_list_projects``."""
    return bool(_PM_TOOL_RE.search(str(mcp_tool_name or "")))


def _state_path() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / STATE_FILE


def load_state() -> Dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("openproject suite: state not saved (%s)", exc)
    _invalidate_cache()
    # This process sees the new decision at once; other processes within the
    # registry's check_fn TTL (~30 s).
    try:
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
    except Exception:
        pass


def _invalidate_cache() -> None:
    global _cached_at
    with _state_lock:
        _cached_at = 0.0


def _current_state() -> Dict[str, Any]:
    """The persisted decision, re-read at most every few seconds (check_fn path)."""
    global _cached_state, _cached_at, _cached_mtime
    now = time.monotonic()
    with _state_lock:
        if now - _cached_at < STATE_RELOAD_SECONDS:
            return _cached_state
        _cached_at = now
        try:
            mtime = _state_path().stat().st_mtime
        except OSError:
            _cached_state, _cached_mtime = {}, None
            return _cached_state
        if mtime != _cached_mtime:
            _cached_state, _cached_mtime = load_state(), mtime
        return _cached_state


def _fresh(state: Dict[str, Any]) -> bool:
    try:
        return time.time() - float(state.get("checked_at") or 0) < STATE_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def decision_fingerprint() -> Optional[tuple]:
    """What is hidden now — part of the tool-definition cache key, so a
    switch (or the decision going stale) re-assembles the tool list."""
    state = _current_state()
    if not state or not _fresh(state):
        return None
    hide_local = tuple(sorted(state.get("hide_local") or []))
    hide_suite = bool(state.get("hide_suite"))
    return (hide_local, hide_suite) if hide_local or hide_suite else None


def tool_hidden(server_name: str, mcp_tool_name: str) -> bool:
    """check_fn hook: hide this server's ``pm_*`` tool for the current turn?"""
    if not is_pm_tool(mcp_tool_name):
        return False
    try:
        state = _current_state()
        if not state or not _fresh(state):
            return False
        if server_name == _SUITE_SERVER:
            return bool(state.get("hide_suite"))
        return server_name in (state.get("hide_local") or [])
    except Exception:
        return False


# --------------------------------------------------------------------------- local servers


def _is_local_openproject(cfg: Dict[str, Any]) -> bool:
    if cfg.get("url") or cfg.get("enabled") is False:
        return False
    parts = [str(cfg.get("command") or "")] + [str(a) for a in (cfg.get("args") or [])]
    return any(_LOCAL_SERVER_MARKER in part for part in parts)


def local_servers(mcp_config: Optional[Dict[str, dict]] = None) -> List[Dict[str, str]]:
    """Configured local OpenProject servers with their instance and token."""
    if mcp_config is None:
        try:
            from tools.mcp_tool import _load_mcp_config

            mcp_config = _load_mcp_config()
        except Exception:
            return []
    out: List[Dict[str, str]] = []
    for name, cfg in (mcp_config or {}).items():
        if not isinstance(cfg, dict) or not _is_local_openproject(cfg):
            continue
        env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
        base_url = str(env.get("OPENPROJECT_BASE_URL") or "").strip()
        token = str(env.get("OPENPROJECT_API_TOKEN") or "").strip()
        if base_url.startswith("${") or token.startswith("${"):
            continue  # unresolved placeholder: not configured
        key = instance_key(base_url)
        if key:
            out.append({"name": name, "base_url": base_url, "instance": key, "token": token})
    return out


# --------------------------------------------------------------------------- suite access


def _find_suite_tool(suffix: str) -> Optional[str]:
    try:
        from tools.registry import registry
    except Exception:
        return None
    prefix = f"mcp_{_SUITE_SERVER}_"
    for entry in registry._snapshot_entries():
        name = getattr(entry, "name", "")
        if name.startswith(prefix) and name.endswith(suffix):
            return name
    return None


def _schema_accepts_string(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    kind = schema.get("type")
    if kind == "string" or (isinstance(kind, list) and "string" in kind):
        return True
    return any(_schema_accepts_string(alt) for alt in (schema.get("anyOf") or schema.get("oneOf") or []))


def suite_accepts_display_ids() -> bool:
    """Does the Suite's ``pm_get_work_package`` take ``PRO-6``-style ids?

    The local server resolves display ids, the Suite server only took integer
    ids at first (AIS-486). Until its schema allows a string id, the local
    server stays available as the fallback for ticket keys (AIS-485).
    """
    name = _find_suite_tool("pm_get_work_package")
    if not name:
        return False
    try:
        from tools.registry import registry

        entry = registry.get_entry(name)
        schema = getattr(entry, "schema", None) or {}
        params = schema.get("parameters") or schema.get("inputSchema") or {}
        return _schema_accepts_string((params.get("properties") or {}).get("id"))
    except Exception:
        return False


def _call_suite_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    import run_agent as _ra

    return _ra.handle_function_call(
        tool_name,
        dict(args),
        "",
        tool_call_id=f"openproject-suite-{uuid.uuid4().hex[:10]}",
        session_id="",
        turn_id="",
        api_request_id="",
        enabled_tools=[tool_name],
        skip_pre_tool_call_hook=True,
        skip_tool_request_middleware=True,
    )


def _payload(raw: Any) -> Dict[str, Any]:
    from agent.memory_facade import _unwrap_mcp_result

    payload = _unwrap_mcp_result(raw)
    if isinstance(payload, str):
        return {"error": payload}
    return payload if isinstance(payload, dict) else {"error": str(payload)[:300]}


def suite_root() -> str:
    """Service root of the Suite the active model runs on (``https://host``)."""
    try:
        from hermes_cli.iamds_suite import active_suite_provider, resolve_suite_endpoint, suite_root_url

        provider = active_suite_provider()
        if not provider:
            return ""
        return suite_root_url(resolve_suite_endpoint(provider).base_url)
    except Exception:
        return ""


def _link_page_base(link_url: str, root: str) -> Optional[str]:
    """``https://suite/connect/openproject/#tok`` → ``https://suite/connect/openproject/``.

    Only for the Suite host the active provider points to, over https (http
    only on a local dev cluster) — the local token never goes anywhere else.
    """
    try:
        link = urlsplit(link_url)
        expected = urlsplit(root) if root else None
    except ValueError:
        return None
    host = (link.hostname or "").lower()
    if not host or expected is None or host != (expected.hostname or "").lower():
        return None
    if link.scheme != "https" and not (link.scheme == "http" and host in _LOCAL_HOSTS):
        return None
    path = link.path if link.path.endswith("/") else link.path + "/"
    return f"{link.scheme}://{link.netloc}{path}"


def _post_json(url: str, body: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    import httpx

    resp = httpx.post(url, json=body, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {}


def _token_fingerprint(root: str, instance: str, token: str) -> str:
    return hashlib.sha256(f"{root}|{instance}|{token}".encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- one check


def _decide(state: Dict[str, Any], locals_: List[Dict[str, str]]) -> None:
    suite = state.get("suite") or {}
    hide_local: List[str] = []
    hide_suite = False
    if suite.get("available"):
        instance = suite.get("instance") or ""
        same = [srv["name"] for srv in locals_ if instance and srv["instance"] == instance]
        if suite.get("linked"):
            if suite.get("display_ids"):
                hide_local = same
        elif same or (suite.get("needs_base_url") and locals_):
            # Not linked yet: the local server serves this domain until the
            # auto-link went through, the unlinked Suite tools would only
            # answer "not linked".
            hide_suite = True
    state["hide_local"] = hide_local
    state["hide_suite"] = hide_suite


def _auto_link(state: Dict[str, Any], link_base: str, root: str, locals_: List[Dict[str, str]]) -> None:
    suite = state["suite"]
    instance = suite.get("instance") or ""
    candidates = [s for s in locals_ if s["token"] and (not instance or s["instance"] == instance)]
    if not candidates:
        return
    local = candidates[0]
    fingerprint = _token_fingerprint(root, local["instance"], local["token"])
    link = state.get("auto_link") or {}
    if link.get("fingerprint") != fingerprint:
        link = {"fingerprint": fingerprint, "attempts": 0}
    if link.get("rejected"):
        return  # this token was refused; wait for a new one
    if time.time() < float(link.get("next_at") or 0):
        return

    body: Dict[str, Any] = {"token": suite.get("link_token"), "api_token": local["token"]}
    if suite.get("needs_base_url"):
        body["base_url"] = local["base_url"]
    try:
        status, data = _post_json(link_base + "api/submit", body)
    except Exception as exc:
        status, data = 0, {"error": "network", "message": str(exc)[:200]}

    link["attempts"] = int(link.get("attempts") or 0) + 1
    link["last_at"] = time.time()
    if status == 200 and data.get("ok"):
        link.update({"linked_at": time.time(), "op_login": data.get("op_login"), "server": local["name"]})
        link.pop("last_error", None)
        suite.update({
            "linked": True,
            "op_login": data.get("op_login"),
            "instance": instance or local["instance"],
            "display_ids": suite_accepts_display_ids(),
        })
        logger.info("openproject suite: linked the Suite account as %s using %s", data.get("op_login"), local["name"])
    else:
        error = str(data.get("error") or f"http_{status}")
        link["last_error"] = error
        if error == "token_invalid":
            link["rejected"] = True
        delay = min(LINK_BACKOFF_BASE_SECONDS * 2 ** (link["attempts"] - 1), LINK_BACKOFF_MAX_SECONDS)
        link["next_at"] = time.time() + delay
        logger.warning("openproject suite: auto-link failed (%s); local OpenProject stays active", error)
    state["auto_link"] = link


_ENV_REF_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _env_refs(cfg: Any) -> set:
    refs: set = set()
    env = cfg.get("env") if isinstance(cfg, dict) else None
    for value in (env or {}).values():
        refs.update(_ENV_REF_RE.findall(str(value)))
    return refs


def uninstall_local_servers(names: List[str]) -> List[str]:
    """Disconnect and uninstall local OpenProject servers the Suite replaced.

    Removes the config entry and the ``${VAR}`` credentials only that server
    referenced (other servers keep theirs). Returns the removed names.
    """
    if not names:
        return []
    try:
        from hermes_cli.config import load_config, remove_env_value
        from hermes_cli.mcp_catalog import uninstall_entry
    except Exception as exc:
        logger.warning("openproject suite: cannot uninstall the local server (%s)", exc)
        return []
    servers = (load_config() or {}).get("mcp_servers") or {}
    removed: List[str] = []
    for name in names:
        cfg = servers.get(name)
        if not isinstance(cfg, dict):
            continue
        try:
            from tools.mcp_tool import disconnect_mcp_server

            disconnect_mcp_server(name)
        except Exception:
            pass
        try:
            if uninstall_entry(name):
                removed.append(name)
        except Exception as exc:
            logger.warning("openproject suite: uninstalling %s failed: %s", name, exc)
    if removed:
        still_used: set = set()
        for other, cfg in servers.items():
            if other not in removed:
                still_used |= _env_refs(cfg)
        for name in removed:
            for var in sorted(_env_refs(servers.get(name)) - still_used):
                try:
                    remove_env_value(var)
                except Exception:
                    pass
        logger.info("openproject suite: removed the local OpenProject server(s) %s — the Suite serves the domain", removed)
    return removed


def run_once(*, config: Optional[dict] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Refresh the Suite view, auto-link if due, persist the decision."""
    now = time.time() if now is None else now
    state = load_state()
    locals_ = local_servers()
    suite: Dict[str, Any] = {"available": False}
    state.pop("hide_local", None)
    state.pop("hide_suite", None)

    tool = _find_suite_tool("pm_link_status") if locals_ else None
    if tool:
        payload = _payload(_call_suite_tool(tool, {}))
        if payload.get("linked") is True:
            suite = {
                "available": True,
                "linked": True,
                "instance": instance_key(payload.get("instance")),
                "own_instance": bool(payload.get("own_instance")),
                "op_login": payload.get("op_login"),
                # Only a Suite that resolves ticket keys replaces the local
                # server (hide + uninstall); until then both stay (AIS-485).
                "display_ids": suite_accepts_display_ids(),
            }
        elif payload.get("link_url"):
            suite = {"available": True, "linked": False}
            root = suite_root()
            link_base = _link_page_base(str(payload["link_url"]), root)
            if link_base:
                fragment = urlsplit(str(payload["link_url"])).fragment
                suite["link_token"] = fragment
                try:
                    status, info = _post_json(link_base + "api/info", {"token": fragment})
                except Exception as exc:
                    status, info = 0, {}
                    logger.debug("openproject suite: link info failed (%s)", exc)
                if status == 200 and info.get("valid"):
                    suite["instance"] = instance_key(info.get("instance_url"))
                    suite["needs_base_url"] = bool(info.get("needs_base_url"))
                state["suite"] = suite
                if auto_link_enabled(config) and suite.get("link_token"):
                    _auto_link(state, link_base, root, locals_)
                suite.pop("link_token", None)
            else:
                logger.info("openproject suite: link page is not on the active Suite host; no auto-link")
        else:
            logger.debug("openproject suite: pm_link_status unusable: %s", str(payload.get("error"))[:200])

    state["suite"] = suite
    state["locals"] = [{"name": s["name"], "instance": s["instance"]} for s in locals_]
    state["checked_at"] = now
    if suite.get("linked"):
        state["next_check_at"] = now + LINKED_RECHECK_SECONDS
    elif suite.get("available"):
        state["next_check_at"] = now + UNLINKED_RECHECK_SECONDS
    else:
        state["next_check_at"] = now + NO_SUITE_RECHECK_SECONDS
    _decide(state, locals_)
    if state.get("hide_local") and suite.get("linked") and remove_local_enabled(config):
        removed = uninstall_local_servers(list(state["hide_local"]))
        if removed:
            remaining = [s for s in locals_ if s["name"] not in removed]
            state["locals"] = [{"name": s["name"], "instance": s["instance"]} for s in remaining]
            state["notice"] = {
                "id": f"openproject-suite-{int(now)}",
                "kind": "local_replaced",
                "removed": removed,
                "instance": suite.get("instance") or "",
                "login": suite.get("op_login") or "",
                "at": now,
            }
            _decide(state, remaining)
    save_state(state)
    return state


def maybe_run(*, config: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Ticker entry point: throttled by the persisted schedule, never raises."""
    if not enabled(config):
        state = load_state()
        if state.get("hide_local") or state.get("hide_suite"):
            state.update({"hide_local": [], "hide_suite": False})
            save_state(state)
        return None
    if not _run_lock.acquire(blocking=False):
        return None
    try:
        state = load_state()
        if time.time() < float(state.get("next_check_at") or 0):
            return None
        return run_once(config=config)
    except Exception as exc:
        logger.debug("openproject suite check failed: %s", exc)
        return None
    finally:
        _run_lock.release()


def status() -> Dict[str, Any]:
    """What the settings page shows: which server serves which domain."""
    state = load_state()
    suite = state.get("suite") or {}
    link = state.get("auto_link") or {}
    return {
        "enabled": enabled(),
        "fresh": _fresh(state),
        "checked_at": state.get("checked_at"),
        "suite_available": bool(suite.get("available")),
        "suite_linked": bool(suite.get("linked")),
        "suite_instance": suite.get("instance") or "",
        "suite_login": suite.get("op_login") or link.get("op_login") or "",
        "locals": state.get("locals") or [],
        "hide_local": state.get("hide_local") or [],
        "hide_suite": bool(state.get("hide_suite")),
        "auto_link_error": link.get("last_error") or "",
        # AIS-483: shown once by the desktop (keyed by id).
        "notice": state.get("notice") or None,
    }


__all__ = [
    "auto_link_enabled",
    "remove_local_enabled",
    "uninstall_local_servers",
    "enabled",
    "instance_key",
    "is_pm_tool",
    "local_servers",
    "maybe_run",
    "run_once",
    "status",
    "tool_hidden",
]
