"""Shared CLI/TUI-safe helpers for background MCP discovery."""

from __future__ import annotations

import os
import threading
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


def wait_for_mcp_discovery(timeout: float = 0.75) -> None:
    """Briefly wait for background MCP discovery before the first tool snapshot."""
    thread = _mcp_discovery_thread
    if thread is None or not thread.is_alive():
        return
    # Explicit env override wins.
    timeout_override = os.environ.get("HERMES_MCP_DISCOVERY_WAIT_SECONDS", "").strip()
    if timeout_override:
        try:
            timeout = max(0.0, float(timeout_override))
        except ValueError:
            pass
    else:
        # Remote MCP endpoints are often slower and need a longer first wait
        # to be present in the initial tool snapshot.
        try:
            from hermes_cli.config import read_raw_config

            mcp_servers = (read_raw_config() or {}).get("mcp_servers")
            if isinstance(mcp_servers, dict):
                remote_timeout = 0.0
                for spec in mcp_servers.values():
                    if not isinstance(spec, dict):
                        continue
                    transport = str(spec.get("transport") or "").strip().lower()
                    if not (spec.get("url") or transport in {"http", "https", "sse", "streamable_http"}):
                        continue
                    try:
                        connect_timeout = float(spec.get("connect_timeout") or 5.0)
                    except Exception:
                        connect_timeout = 5.0
                    remote_timeout = max(remote_timeout, connect_timeout + 2.0)
                if remote_timeout > 0.0:
                    timeout = max(timeout, min(remote_timeout, 20.0))
        except Exception:
            pass

    thread.join(timeout=timeout)
