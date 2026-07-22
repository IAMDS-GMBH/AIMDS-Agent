#!/usr/bin/env python3
"""Enforce AIMDS config defaults on install/reinstall.

Usage:
    python upsert_aimds_defaults.py <config_path>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_AIMDS_DEFAULTS_VERSION = 8
_AIMDS_DEFAULTS_VERSION_KEY = "aimds_defaults_version"


_AIMDS_TOOL_INCLUDE_RAW = [
    # Canonical MCP tool names as advertised by the IAMDS gateway itself.
    # Keep this list intentionally small to avoid noisy tool surfaces.
    "aimds_kb_kb_search",
    "aimds_kb_kb_get_topic",
    "aimds_kb_kb_get_related",
    "mcp_memory_memory_context",
    "mcp_memory_memory_get",
    "mcp_memory_memory_list",
    "mcp_memory_memory_upsert",
    "mcp_memory_memory_delete",
]

_AIMDS_TOOL_INCLUDE_LEGACY = (
    (
        "kb_search",
        "kb_get_topic",
        "kb_get_related",
        "memory_get",
        "memory_list",
        "memory_upsert",
        "memory_delete",
    ),
    (
        "kb_search",
        "kb_get_topic",
        "kb_get_related",
        "memory_context",
        "memory_get",
        "memory_list",
        "memory_upsert",
        "memory_delete",
    ),
)


def _ensure_dict(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _sanitize_mcp_name_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def _build_aimds_tool_include(server_name: str) -> list[str]:
    """Build include list with full and raw names for robust matching."""
    safe_server_name = _sanitize_mcp_name_component(server_name)
    prefixed = [
        f"mcp_{safe_server_name}_{_sanitize_mcp_name_component(tool_name)}"
        for tool_name in _AIMDS_TOOL_INCLUDE_RAW
    ]
    return prefixed + list(_AIMDS_TOOL_INCLUDE_RAW)


def _coerce_version(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _resolve_target_mcp_server_name(mcp_servers: dict) -> str:
    if not isinstance(mcp_servers, dict):
        return "IAMDS"

    # Name-agnostic primary rule: target whichever MCP entry declares
    # provider: iamds.
    for name, cfg in mcp_servers.items():
        if isinstance(cfg, dict) and str(cfg.get("provider", "")).strip().lower() == "iamds":
            return str(name)

    for preferred in ("IAMDS", "memory", "aimds-gateway", "remoteMCP", "remote"):
        if preferred in mcp_servers and isinstance(mcp_servers.get(preferred), dict):
            return preferred

    for name, cfg in mcp_servers.items():
        if isinstance(cfg, dict):
            return str(name)

    return "IAMDS"


def _is_upsert_only_aimds_gateway(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    # Safe cleanup criterion: this looks like a synthetic stub created only by
    # the defaults-upsert (tools filters, no transport/url/command identity).
    identity_keys = {"url", "transport", "command", "provider", "args"}
    if any(k in entry for k in identity_keys):
        return False
    tools = entry.get("tools")
    if not isinstance(tools, dict):
        return False
    include = tools.get("include")
    if isinstance(include, list):
        raw_include = set(_AIMDS_TOOL_INCLUDE_RAW)
        if raw_include.issubset(set(include)):
            return True
        include_tuple = tuple(include)
        if include_tuple in _AIMDS_TOOL_INCLUDE_LEGACY:
            return True
    return False


def upsert_aimds_defaults(config: dict) -> dict:
    cfg = config if isinstance(config, dict) else {}

    tools = _ensure_dict(cfg, "tools")
    tool_search = _ensure_dict(tools, "tool_search")
    tool_search["enabled"] = "on"
    tool_search["threshold_pct"] = 10
    tool_search["search_default_limit"] = 5
    tool_search["max_search_limit"] = 20

    prompt_caching = _ensure_dict(cfg, "prompt_caching")
    prompt_caching["cache_ttl"] = "5m"

    memory = _ensure_dict(cfg, "memory")
    memory["enforce_initial_memory_context"] = True
    memory["session_start_compact_workspace_hydration"] = True

    auxiliary = _ensure_dict(cfg, "auxiliary")
    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        aux_slot = _ensure_dict(auxiliary, slot)
        aux_slot["provider"] = "openai_compatible"
        aux_slot["base_url"] = "https://<litellm-host>/v1"
        aux_slot["model"] = "<litellm-fast-model>"

    mcp_servers = _ensure_dict(cfg, "mcp_servers")
    target_name = _resolve_target_mcp_server_name(mcp_servers)
    target_server = _ensure_dict(mcp_servers, target_name)
    aimds_tools = _ensure_dict(target_server, "tools")
    aimds_tools["include"] = _build_aimds_tool_include(target_name)
    aimds_tools["resources"] = False
    aimds_tools["prompts"] = False

    # Repair from v1 behavior: avoid introducing a separate synthetic
    # "aimds-gateway" MCP entry when the real server is IAMDS.
    if target_name != "aimds-gateway":
        legacy = mcp_servers.get("aimds-gateway")
        if _is_upsert_only_aimds_gateway(legacy):
            mcp_servers.pop("aimds-gateway", None)

    return cfg


def migrate_aimds_defaults(config: dict) -> tuple[dict, bool, str]:
    cfg = config if isinstance(config, dict) else {}
    current = _coerce_version(cfg.get(_AIMDS_DEFAULTS_VERSION_KEY))
    if current >= _AIMDS_DEFAULTS_VERSION:
        return cfg, False, f"aimds-defaults: already current (v{current})"

    cfg = upsert_aimds_defaults(cfg)
    cfg[_AIMDS_DEFAULTS_VERSION_KEY] = _AIMDS_DEFAULTS_VERSION
    return cfg, True, f"aimds-defaults: applied v{_AIMDS_DEFAULTS_VERSION} (from v{current})"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0]} <config_path>", file=sys.stderr)
        return 1

    path = Path(argv[1]).expanduser()
    if not path.exists():
        print(f"config-not-found: {path}", file=sys.stderr)
        return 2

    try:
        raw = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw) if raw.strip() else {}
    except OSError as exc:
        print(f"read-error: {exc}", file=sys.stderr)
        return 3
    except yaml.YAMLError as exc:
        print(f"yaml-parse-error: {exc}", file=sys.stderr)
        return 4

    updated, _changed, status = migrate_aimds_defaults(parsed)
    try:
        path.write_text(
            yaml.safe_dump(updated, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"write-error: {exc}", file=sys.stderr)
        return 5

    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
