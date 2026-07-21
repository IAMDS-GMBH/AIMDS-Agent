#!/usr/bin/env python3
"""Enforce AIMDS config defaults on install/reinstall.

Usage:
    python upsert_aimds_defaults.py <config_path>
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


_AIMDS_TOOL_INCLUDE = [
    "kb_search",
    "kb_get_topic",
    "kb_get_related",
    "memory_get",
    "memory_list",
    "memory_upsert",
    "memory_delete",
]


def _ensure_dict(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


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

    auxiliary = _ensure_dict(cfg, "auxiliary")
    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        aux_slot = _ensure_dict(auxiliary, slot)
        aux_slot["provider"] = "openai_compatible"
        aux_slot["base_url"] = "https://<litellm-host>/v1"
        aux_slot["model"] = "<litellm-fast-model>"

    mcp_servers = _ensure_dict(cfg, "mcp_servers")
    aimds_gateway = _ensure_dict(mcp_servers, "aimds-gateway")
    aimds_tools = _ensure_dict(aimds_gateway, "tools")
    aimds_tools["include"] = list(_AIMDS_TOOL_INCLUDE)
    aimds_tools["resources"] = False
    aimds_tools["prompts"] = False

    return cfg


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

    updated = upsert_aimds_defaults(parsed)
    try:
        path.write_text(
            yaml.safe_dump(updated, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"write-error: {exc}", file=sys.stderr)
        return 5

    print("aimds-defaults: applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
