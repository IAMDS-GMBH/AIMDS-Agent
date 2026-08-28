"""Shared CLI/TUI-safe helpers for background MCP discovery."""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

_mcp_discovery_lock = threading.Lock()
_mcp_discovery_started = False
_mcp_discovery_thread: Optional[threading.Thread] = None


def _has_configured_mcp_servers() -> bool:
    """Cheap config probe so non-MCP users avoid importing the MCP stack."""
    try:
        from hermes_cli.config import read_raw_config

        mcp_servers = (read_raw_config() or {}).get("mcp_servers")
        return isinstance(mcp_servers, dict) and len(mcp_servers) > 0
    except Exception:
        # Be conservative: if config probing fails, try discovery in the
        # background so startup still can't block.
        return True


def start_background_mcp_discovery(*, logger, thread_name: str) -> None:
    """Spawn one shared background MCP discovery thread for this process."""
    global _mcp_discovery_started, _mcp_discovery_thread

    with _mcp_discovery_lock:
        if _mcp_discovery_started:
            return
        _mcp_discovery_started = True
        if not _has_configured_mcp_servers():
            return

        def _discover() -> None:
            try:
                from tools.mcp_tool import discover_mcp_tools

                discover_mcp_tools()
            except Exception:
                logger.debug("Background MCP tool discovery failed", exc_info=True)

        thread = threading.Thread(
            target=_discover,
            name=thread_name,
            daemon=True,
        )
        _mcp_discovery_thread = thread
        thread.start()


# A stdio server that has to fetch its package first (npx/uvx) is not ready in
# well under a second. Anything already on disk usually is.
_COLD_START_LAUNCHERS = ("npx", "uvx", "uv", "pnpm", "bunx")
_STDIO_COLD_BUDGET = 8.0
_STDIO_WARM_BUDGET = 3.0
_DISCOVERY_BUDGET_CAP = 20.0
# Give up early when nothing is arriving any more. Without this a single
# permanently broken server would cost every session the full budget.
_DISCOVERY_STALL_GRACE = 2.5


def _enabled_mcp_server_specs() -> dict:
    """Enabled `mcp_servers` entries, or {} when config is unreadable."""
    try:
        from hermes_cli.config import read_raw_config

        servers = (read_raw_config() or {}).get("mcp_servers")
    except Exception:
        return {}
    if not isinstance(servers, dict):
        return {}

    out = {}
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        enabled = spec.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in {"true", "1", "yes"}
        if enabled:
            out[str(name)] = spec

    return out


def _discovery_budget(specs: dict) -> float:
    """Longest plausible time until every configured server has answered.

    Previously only *remote* servers extended the wait and stdio servers were
    capped at the 0.75s default. A cold `npx -y <pkg>` cannot list its tools in
    that window, so the first tool snapshot was taken without it — the session
    then ran with the server missing while the desktop, which reads config
    rather than the live registry, still displayed its tools.
    """
    budget = 0.0
    for spec in specs.values():
        transport = str(spec.get("transport") or "").strip().lower()
        is_remote = bool(spec.get("url")) or transport in {"http", "https", "sse", "streamable_http"}
        try:
            configured = float(spec.get("connect_timeout") or 0.0)
        except Exception:
            configured = 0.0

        if is_remote:
            budget = max(budget, (configured or 5.0) + 2.0)
            continue

        if configured:
            budget = max(budget, configured)
            continue

        command = str(spec.get("command") or "").strip().lower()
        launcher = command.rsplit("/", 1)[-1]
        cold = launcher in _COLD_START_LAUNCHERS
        budget = max(budget, _STDIO_COLD_BUDGET if cold else _STDIO_WARM_BUDGET)

    return min(budget, _DISCOVERY_BUDGET_CAP)


def _discovered_server_names() -> Optional[set]:
    """Names of servers already registered, or None if unavailable."""
    try:
        from tools import mcp_tool

        return set(getattr(mcp_tool, "_servers", {}) or {})
    except Exception:
        return None


def wait_for_mcp_discovery(timeout: Optional[float] = None) -> None:
    """Wait for background MCP discovery before the first tool snapshot.

    Returns as soon as every enabled server is registered, so a warm start
    stays fast; falls back to the budget deadline when a server never answers.
    A missing server must not silently shrink the toolset for a whole session.
    """
    thread = _mcp_discovery_thread
    if thread is None or not thread.is_alive():
        return

    override = os.environ.get("HERMES_MCP_DISCOVERY_WAIT_SECONDS", "").strip()
    if override:
        try:
            timeout = max(0.0, float(override))
        except ValueError:
            timeout = None

    specs = _enabled_mcp_server_specs()
    if timeout is None:
        timeout = _discovery_budget(specs) or 0.75

    expected = set(specs)
    deadline = time.monotonic() + timeout
    last_progress = time.monotonic()
    seen = 0
    while True:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            return
        if expected:
            discovered = _discovered_server_names()
            if discovered is not None:
                if expected.issubset(discovered):
                    return
                if len(discovered) > seen:
                    seen = len(discovered)
                    last_progress = now
                elif now - last_progress > _DISCOVERY_STALL_GRACE:
                    # Servers stopped arriving; the rest are not coming.
                    return
        thread.join(timeout=min(0.05, remaining))
        if not thread.is_alive():
            return
