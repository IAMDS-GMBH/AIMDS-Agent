#!/usr/bin/env python3
"""Enforce AIMDS config defaults on install/reinstall.

Usage:
    python upsert_aimds_defaults.py <config_path>
"""

from __future__ import annotations

from copy import deepcopy
import re
import sys
from pathlib import Path

import yaml

# This script runs as a standalone subprocess (see
# hermes_cli/main.py::_apply_aimds_defaults_after_update, which invokes it
# via `subprocess.run([sys.executable, script_path, config_path])`), so it
# does not import the rest of the hermes_cli package. utils.py is a
# top-level module at the repo root; when hermes-agent is installed
# (editable or otherwise) it's importable directly. The sys.path fallback
# below only matters for the rare case where this script is invoked from
# an environment where the package isn't on sys.path at all.
try:
    from utils import advisory_file_lock, atomic_yaml_write
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from utils import advisory_file_lock, atomic_yaml_write

_AIMDS_DEFAULTS_VERSION = 14
_AIMDS_DEFAULTS_VERSION_KEY = "aimds_defaults_version"


_AIMDS_TOOL_INCLUDE_RAW = [
    # Canonical AIMDS default tools (KB, Memory, WebSearch).
    # Flexible alias matching in mcp_tool.py automatically handles server
    # prefixes (e.g. mcp_IAMDS_..., aimds_kb_..., mcp_memory_...).
    "kb_search",
    "kb_get_topic",
    "kb_list_topics",
    "kb_get_recent",
    "kb_get_related",
    "kb_get_tags",
    "kb_get_backlinks",
    "kb_get_graph",
    "memory_context",
    "memory_get",
    "memory_list",
    "memory_save",
    "memory_read",
    "memory_upsert",
    "memory_delete",
    "memory_search",
    "memory_manage",
    "memory_backlinks",
    "memory_transfer",
    "memory_meta",
    "memory_agent",
    "memory_summarize_session",
    "skill",
    "web_search",
    "web_fetch",
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
    """Build include list of base tool names. Alias matching handles prefixing."""
    return list(dict.fromkeys(_AIMDS_TOOL_INCLUDE_RAW))


def _coerce_version(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _resolve_target_mcp_server_name(mcp_servers: dict) -> str:
    if not isinstance(mcp_servers, dict):
        return "AIMDSSuiteMCP"

    # Name-agnostic primary rule: target whichever MCP entry declares
    # provider: iamds.
    for name, cfg in mcp_servers.items():
        if isinstance(cfg, dict) and str(cfg.get("provider", "")).strip().lower() == "iamds":
            return str(name)

    for preferred in ("AIMDSSuiteMCP", "IAMDS", "memory", "aimds-gateway", "remoteMCP", "remote"):
        if preferred in mcp_servers and isinstance(mcp_servers.get(preferred), dict):
            return preferred

    for name, cfg in mcp_servers.items():
        if isinstance(cfg, dict):
            return str(name)

    return "AIMDSSuiteMCP"


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
    tool_search["search_default_limit"] = 8
    tool_search["max_search_limit"] = 20

    prompt_caching = _ensure_dict(cfg, "prompt_caching")
    prompt_caching["cache_ttl"] = "5m"

    memory = _ensure_dict(cfg, "memory")
    memory["enforce_initial_memory_context"] = False
    memory["session_start_compact_workspace_hydration"] = False
    memory["session_start_bootstrap_contract_enabled"] = False

    auxiliary = _ensure_dict(cfg, "auxiliary")
    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        aux_slot = auxiliary.get(slot)
        if isinstance(aux_slot, dict):
            if "<litellm-host>" in str(aux_slot.get("base_url", "")) or "<litellm-fast-model>" in str(aux_slot.get("model", "")):
                auxiliary.pop(slot, None)

    mcp_servers = _ensure_dict(cfg, "mcp_servers")

    # Clean up legacy synthetic IAMDS stubs before resolving target name
    if "IAMDS" in mcp_servers:
        iamds_entry = mcp_servers.get("IAMDS")
        if _is_upsert_only_aimds_gateway(iamds_entry):
            mcp_servers.pop("IAMDS", None)
        elif isinstance(iamds_entry, dict) and "AIMDSSuiteMCP" not in mcp_servers:
            # Migrate real IAMDS entry to AIMDSSuiteMCP
            mcp_servers["AIMDSSuiteMCP"] = iamds_entry
            mcp_servers.pop("IAMDS", None)

    target_name = _resolve_target_mcp_server_name(mcp_servers)
    target_server = _ensure_dict(mcp_servers, target_name)

    headers = _ensure_dict(target_server, "headers")
    auth_val = str(headers.get("Authorization") or "").strip()
    if not auth_val or auth_val == "******" or auth_val.endswith("******"):
        headers["Authorization"] = "${IAMDS_LITELLM_API_KEY}"

    aimds_tools = _ensure_dict(target_server, "tools")
    aimds_tools["include"] = _build_aimds_tool_include(target_name)
    aimds_tools["resources"] = False
    aimds_tools["prompts"] = False

    # Repair from v1 behavior: avoid introducing a separate synthetic
    # "aimds-gateway" or legacy "IAMDS" stub when the real server is AIMDSSuiteMCP.
    if target_name != "aimds-gateway":
        legacy = mcp_servers.get("aimds-gateway")
        if _is_upsert_only_aimds_gateway(legacy):
            mcp_servers.pop("aimds-gateway", None)

    if target_name != "IAMDS":
        legacy_iamds = mcp_servers.get("IAMDS")
        if _is_upsert_only_aimds_gateway(legacy_iamds) or "AIMDSSuiteMCP" in mcp_servers:
            mcp_servers.pop("IAMDS", None)

    return cfg


def migrate_aimds_defaults(config: dict) -> tuple[dict, bool, str]:
    cfg = config if isinstance(config, dict) else {}
    current = _coerce_version(cfg.get(_AIMDS_DEFAULTS_VERSION_KEY))
    before = deepcopy(cfg)
    cfg = upsert_aimds_defaults(cfg)
    if current >= _AIMDS_DEFAULTS_VERSION:
        changed = cfg != before
        status = (
            f"aimds-defaults: enforced policy v{_AIMDS_DEFAULTS_VERSION} (already current v{current})"
            if changed
            else f"aimds-defaults: already current (v{current})"
        )
        return cfg, changed, status

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
        # Root-cause fix (data-loss bug): this used to be a non-atomic
        # path.write_text() call. If this subprocess (or another writer,
        # e.g. hermes_cli.config.save_config running concurrently in the
        # desktop app / gateway) was interrupted or raced mid-write, the
        # two writes could interleave and splice YAML keys onto one
        # physical line, producing an unparseable config.yaml that then
        # gets silently replaced by DEFAULT_CONFIG on next load. Use the
        # same atomic temp-file + fsync + os.replace primitive as
        # hermes_cli/config.py::save_config(), guarded by the same
        # advisory cross-process lock, so a write here is always either
        # fully applied or not applied at all.
        with advisory_file_lock(path):
            atomic_yaml_write(path, updated, sort_keys=False)
    except OSError as exc:
        print(f"write-error: {exc}", file=sys.stderr)
        return 5

    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
