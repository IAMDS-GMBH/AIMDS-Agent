#!/usr/bin/env python3
"""Enforce AIMDS config defaults on install/reinstall.

Usage:
    python upsert_aimds_defaults.py <config_path>
"""

from __future__ import annotations

from copy import deepcopy
import os
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

_AIMDS_DEFAULTS_VERSION = 17
_AIMDS_DEFAULTS_VERSION_KEY = "aimds_defaults_version"

# ---------------------------------------------------------------------------
# Two phases, by who owns a key.
#
# The end user never edits config.yaml by hand. What they *can* change is
# the Desktop GUI allowlist (apps/desktop/src/app/settings/constants.ts,
# SECTIONS) — model + auxiliary slots, chat, workspace, safety, memory,
# voice, MCP, providers, the Skills page, and the warning-gated "Advanced"
# section. Those keys are the user's: a deploy default is applied ONCE
# (a versioned one-shot step in _ONE_SHOT_STEPS) and only while the value
# is still at delivery state; afterwards the GUI choice stands.
#
# Everything else is CLI-only or normally never configured at all. Those
# keys are the deploy standard: ``upsert_aimds_defaults`` writes them on
# EVERY run, and ``_AIMDS_ENFORCED_POLICY`` lists them so the update log
# can say what is not persistent.
# ---------------------------------------------------------------------------
_AIMDS_ENFORCED_POLICY = (
    "tools.tool_search.enabled",
    "tools.tool_search.threshold_pct",
    "tools.tool_search.search_default_limit",
    "tools.tool_search.max_search_limit",
    "prompt_caching.cache_ttl",
    "prompt_caching.message_ttl",
    "memory.enforce_initial_memory_context",
    "memory.session_start_compact_workspace_hydration",
    "memory.session_start_bootstrap_contract_enabled",
    "memory.backend",
    "memory.local_tool",
    "curator.prune_builtins",
    "auxiliary.goal_judge",
    "mcp_servers",  # the AIMDS Suite MCP entry (legacy names are folded into it)
)

# Auxiliary model slots ("compression", "title_generation", ...) run the
# cheap background calls: compaction summaries, titles, approval checks,
# MCP helper calls. Left on {provider: auto, model: ""} they resolve to the
# main model (AIMDS-Suite-Auto — the expensive router) or fail outright when
# the provider is marked unhealthy. The deploy standard points them at the
# configured AIMDS Suite LiteLLM provider with a fast model.
#
# Which fast model is NOT a constant. The LiteLLM catalog lives in
# AIMDS-Suite/k8s/base/go-orchestrator/litellm-base.yaml and the proxy's
# model_filter_hook hides models per API key, so every key sees a different
# subset that can change at any time. The list below is a preference order
# over today's catalog; the actual choice is the first entry the key can
# see (hermes_cli.models.cached_provider_model_ids — the provider's
# /v1/models, cached per provider + credential fingerprint), else the main
# model. Never a model the key cannot see, never a guess.
_AIMDS_FAST_AUX_PREFERENCE = ("claude-haiku-4.5", "gpt-5-mini", "gemini-3.6-flash")
_AIMDS_AUTO_MODEL = "AIMDS-Suite-Auto"
# Slots the Desktop GUI edits (model-settings.tsx) → one-shot, GUI owns them.
_AIMDS_GUI_AUX_SLOTS = ("compression", "title_generation", "approval", "mcp")
# Slots no GUI reaches → policy, enforced on every run.
_AIMDS_POLICY_AUX_SLOTS = ("goal_judge",)

# The Desktop "Advanced" section (constants.ts SECTIONS → advanced). Brought
# to the shipped default once in v16; the installer seeds none of these, so
# DEFAULT_CONFIG is the standard.
_AIMDS_ADVANCED_KEYS = (
    "toolsets",
    "terminal.backend",
    "terminal.timeout",
    "terminal.docker_image",
    "terminal.singularity_image",
    "terminal.modal_image",
    "terminal.daytona_image",
    "tool_output.max_bytes",
    "tool_output.max_lines",
    "tool_output.max_line_length",
    "checkpoints.max_snapshots",
    "agent.max_turns",
    "agent.api_max_retries",
    "agent.service_tier",
    "agent.tool_use_enforcement",
    "delegation.model",
    "delegation.provider",
    "delegation.max_iterations",
    "delegation.max_concurrent_children",
    "delegation.child_timeout_seconds",
    "delegation.reasoning_effort",
    "updates.non_interactive_local_changes",
)

_MISSING = object()


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


def _build_iamds_mcp_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base or "<litellm-host>" in base:
        return ""
    for suffix in ("/litellm/v1", "/litellm/mcp", "/v1"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return f"{base}/litellm/mcp/"


def _resolve_target_mcp_server_name(mcp_servers: dict) -> str:
    if not isinstance(mcp_servers, dict):
        return "AIMDSSuiteMCP"

    if "AIMDSSuiteMCP" in mcp_servers and isinstance(mcp_servers.get("AIMDSSuiteMCP"), dict):
        return "AIMDSSuiteMCP"

    # Check for legacy names that migrate to AIMDSSuiteMCP
    for legacy in ("IAMDS", "AIMDS", "memory", "aimds-gateway", "remoteMCP", "remote"):
        if legacy in mcp_servers and isinstance(mcp_servers.get(legacy), dict):
            return "AIMDSSuiteMCP"

    for name, cfg in mcp_servers.items():
        if isinstance(cfg, dict) and str(cfg.get("provider", "")).strip().lower() in ("iamds", "aimds"):
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


def _get_path(cfg: dict, dotted: str):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _set_path(cfg: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = _ensure_dict(node, part)
    node[parts[-1]] = value


def _resolve_aimds_aux_provider(cfg: dict) -> str:
    """The configured AIMDS Suite provider id, or "" when the main model
    runs elsewhere (then the aux slots are not ours to touch)."""
    model = cfg.get("model")
    provider = str(model.get("provider") or "").strip() if isinstance(model, dict) else ""
    if provider.startswith("aimds-suite-") or provider == "iamds-litellm":
        return provider
    return ""


def _main_model(cfg: dict) -> str:
    model = cfg.get("model")
    default = str(model.get("default") or "").strip() if isinstance(model, dict) else ""
    return default or _AIMDS_AUTO_MODEL


def _available_aimds_models(provider: str) -> list[str]:
    """Model ids the configured key can see on this provider (lowercased).

    Empty when the package is not importable (install.sh bootstrap) or the
    provider cannot be reached and nothing is cached for this key.
    """
    try:
        from hermes_cli.models import cached_provider_model_ids
    except Exception:
        return []
    try:
        ids = cached_provider_model_ids(provider) or []
    except Exception:
        return []
    return [str(m).strip().lower() for m in ids if str(m).strip()]


def _model_list_fetcher():
    """One provider lookup per run, shared by both phases."""
    cache: dict = {}

    def fetch(provider: str) -> list[str]:
        if provider not in cache:
            cache[provider] = _available_aimds_models(provider)
        return cache[provider]

    return fetch


def _pick_fast_aux_model(available: list[str], main_model: str) -> str:
    for candidate in _AIMDS_FAST_AUX_PREFERENCE:
        if candidate.lower() in available:
            return candidate
    return main_model


def _is_unconfigured_aux_slot(slot: object) -> bool:
    """Delivery state: absent, or {provider: auto|"", model: "", base_url: ""}."""
    if not isinstance(slot, dict):
        return True
    provider = str(slot.get("provider") or "").strip().lower()
    return provider in ("", "auto") and not str(slot.get("model") or "").strip() and not str(slot.get("base_url") or "").strip()


def _is_aimds_managed_aux_slot(slot: object, cfg: dict) -> bool:
    """A slot we set ourselves: AIMDS provider + a model from our choice set."""
    if not isinstance(slot, dict):
        return False
    provider = str(slot.get("provider") or "").strip()
    if not (provider.startswith("aimds-suite-") or provider == "iamds-litellm"):
        return False
    model = str(slot.get("model") or "").strip().lower()
    ours = {m.lower() for m in _AIMDS_FAST_AUX_PREFERENCE} | {_main_model(cfg).lower(), _AIMDS_AUTO_MODEL.lower()}
    return model in ours


def _aux_status_line(provider: str, model: str, available: list[str]) -> str:
    source = f"provider lists {len(available)} models" if available else "fallback: main model"
    return f"auxiliary → {provider} / {model} ({source})"


def _apply_aux_policy(cfg: dict, fetch) -> list[str]:
    """Every run: goal_judge (no GUI reaches it) on the fast model; heal the
    GUI slots we set earlier when their model vanished or the main provider
    moved (prod → staging). Never touches a slot the user chose."""
    lines: list[str] = []
    aux_provider = _resolve_aimds_aux_provider(cfg)
    if not aux_provider:
        return lines
    auxiliary = _ensure_dict(cfg, "auxiliary")
    available = fetch(aux_provider)
    main_model = _main_model(cfg)
    pick = _pick_fast_aux_model(available, main_model)
    preferred = {m.lower() for m in _AIMDS_FAST_AUX_PREFERENCE}

    for slot in _AIMDS_POLICY_AUX_SLOTS:
        existing = auxiliary.get(slot)
        existing = existing if isinstance(existing, dict) else {}
        if not available and _is_aimds_managed_aux_slot(existing, cfg):
            continue  # offline: keep what we set last time
        desired = {**existing, "provider": aux_provider, "model": pick}
        if desired != existing:
            auxiliary[slot] = desired
            lines.append(_aux_status_line(aux_provider, pick, available))

    for slot in _AIMDS_GUI_AUX_SLOTS:
        existing = auxiliary.get(slot)
        if not _is_aimds_managed_aux_slot(existing, cfg):
            continue
        model = str(existing.get("model") or "").strip()
        changed = False
        if existing.get("provider") != aux_provider:
            existing["provider"] = aux_provider
            changed = True
        if available and model.lower() in preferred and model.lower() not in available:
            existing["model"] = pick
            changed = True
        if changed:
            lines.append(_aux_status_line(aux_provider, str(existing["model"]), available))
    return lines


def upsert_aimds_defaults(config: dict, fetch=None, lines: list[str] | None = None) -> dict:
    """The every-run policy (CLI-only / never-configured keys, see
    ``_AIMDS_ENFORCED_POLICY``). ``lines`` collects status lines."""
    cfg = config if isinstance(config, dict) else {}
    fetch = fetch or _model_list_fetcher()
    lines = lines if lines is not None else []

    tools = _ensure_dict(cfg, "tools")
    tool_search = _ensure_dict(tools, "tool_search")
    tool_search["enabled"] = "on"
    tool_search["threshold_pct"] = 10
    tool_search["search_default_limit"] = 8
    tool_search["max_search_limit"] = 20

    # Prefix tier (tools + system prompt, written once per session) at 1h so
    # pauses > 5 min and cron runs within the hour still hit; the moving
    # conversation breakpoints stay on 5m.
    prompt_caching = _ensure_dict(cfg, "prompt_caching")
    prompt_caching["cache_ttl"] = "1h"
    prompt_caching["message_ttl"] = "5m"

    memory = _ensure_dict(cfg, "memory")
    # The session-start memory_context load is deterministic (the SOUL asks
    # for it and the runtime does it); the compact workspace hydration reads
    # thisweek/findings/active projects that the vault template is built for.
    memory["enforce_initial_memory_context"] = True
    memory["session_start_compact_workspace_hydration"] = True
    memory["session_start_bootstrap_contract_enabled"] = False
    memory["backend"] = "auto"
    memory["local_tool"] = "auto"

    # The curator may archive/absorb agent-created skills, never the skills
    # we ship: those come back on every update anyway, and pruning them only
    # "sticks" through a suppression list that fights the re-seeder.
    curator = _ensure_dict(cfg, "curator")
    curator["prune_builtins"] = False

    auxiliary = _ensure_dict(cfg, "auxiliary")
    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        aux_slot = auxiliary.get(slot)
        if isinstance(aux_slot, dict):
            if "<litellm-host>" in str(aux_slot.get("base_url", "")) or "<litellm-fast-model>" in str(aux_slot.get("model", "")):
                auxiliary.pop(slot, None)
    lines.extend(_apply_aux_policy(cfg, fetch))

    mcp_servers = _ensure_dict(cfg, "mcp_servers")

    target_name = _resolve_target_mcp_server_name(mcp_servers)
    target_server = mcp_servers.get(target_name)
    if not isinstance(target_server, dict):
        target_server = {}

    # Migrate and remove any legacy server entries (IAMDS, AIMDS, aimds-gateway, remote, etc.)
    legacy_keys = {"IAMDS", "iamds", "AIMDS", "aimds", "aimds-gateway", "remoteMCP", "remote", "memory"}
    for legacy in list(mcp_servers.keys()):
        if legacy in legacy_keys and legacy != target_name:
            legacy_entry = mcp_servers.pop(legacy, None)
            if isinstance(legacy_entry, dict):
                # Copy properties from legacy entry if target_server lacks them
                for k, v in legacy_entry.items():
                    if k not in target_server or not target_server[k]:
                        target_server[k] = v

    mcp_servers[target_name] = target_server

    target_server["provider"] = "iamds"
    target_server["connect_timeout"] = target_server.get("connect_timeout") or 60
    target_server["timeout"] = target_server.get("timeout") or 180
    target_server["trusted"] = True

    headers = _ensure_dict(target_server, "headers")
    auth_val = str(headers.get("Authorization") or "").strip()
    if not auth_val or auth_val == "******" or auth_val.endswith("******"):
        headers["Authorization"] = "${IAMDS_LITELLM_API_KEY}"

    aimds_tools = _ensure_dict(target_server, "tools")
    aimds_tools["include"] = _build_aimds_tool_include(target_name)
    aimds_tools["resources"] = False
    aimds_tools["prompts"] = False

    # Resolve URL if missing or placeholder
    url_val = str(target_server.get("url") or "").strip()
    if not url_val or "<litellm-host>" in url_val:
        base_url = str(
            cfg.get("base_url")
            or cfg.get("providers", {}).get("iamds-litellm", {}).get("base_url")
            or os.environ.get("IAMDS_LITELLM_BASE_URL", "")
            or os.environ.get("HERMES_BOOTSTRAP_BASE_URL", "")
            or ""
        ).strip()
        derived_url = _build_iamds_mcp_url(base_url)
        if derived_url:
            target_server["url"] = derived_url
        else:
            target_server["url"] = "https://suite.iamds.com/litellm/mcp/"

    return cfg


class _OneShotDeferred(Exception):
    """A one-shot step could not run (e.g. package not importable); the
    version is left unstamped so the next update retries."""


def _one_shot_v16(cfg: dict, fetch) -> list[str]:
    """GUI-owned keys, applied once: aux slots still at delivery state go to
    the AIMDS provider + fast model; the "Advanced" section returns to the
    shipped default."""
    lines: list[str] = []

    aux_provider = _resolve_aimds_aux_provider(cfg)
    if aux_provider:
        auxiliary = _ensure_dict(cfg, "auxiliary")
        available = fetch(aux_provider)
        pick = _pick_fast_aux_model(available, _main_model(cfg))
        for slot in _AIMDS_GUI_AUX_SLOTS:
            existing = auxiliary.get(slot)
            if not _is_unconfigured_aux_slot(existing):
                continue  # the user's GUI choice stands
            base = existing if isinstance(existing, dict) else {}
            auxiliary[slot] = {**base, "provider": aux_provider, "model": pick}
        lines.append(_aux_status_line(aux_provider, pick, available))

    try:
        from hermes_cli.config import DEFAULT_CONFIG
    except Exception as exc:
        raise _OneShotDeferred(f"hermes_cli.config not importable: {exc}") from exc
    for key in _AIMDS_ADVANCED_KEYS:
        current = _get_path(cfg, key)
        default = _get_path(DEFAULT_CONFIG, key)
        if current is _MISSING or default is _MISSING or current == default:
            continue
        _set_path(cfg, key, deepcopy(default))
        lines.append(f"advanced reset: {key} {current!r} → {default!r}")
    return lines


_ONE_SHOT_STEPS = {16: _one_shot_v16}


def apply_one_shot_defaults(cfg: dict, from_version: int, fetch=None) -> list[str]:
    """Run every one-shot step above ``from_version`` in order."""
    fetch = fetch or _model_list_fetcher()
    lines: list[str] = []
    for version in sorted(_ONE_SHOT_STEPS):
        if from_version < version <= _AIMDS_DEFAULTS_VERSION:
            lines.extend(_ONE_SHOT_STEPS[version](cfg, fetch))
    return lines


def _enforced_changes(before: dict, after: dict) -> list[str]:
    return [p for p in _AIMDS_ENFORCED_POLICY if _get_path(before, p) != _get_path(after, p)]


def migrate_aimds_defaults(config: dict) -> tuple[dict, bool, str]:
    cfg = config if isinstance(config, dict) else {}
    current = _coerce_version(cfg.get(_AIMDS_DEFAULTS_VERSION_KEY))
    before = deepcopy(cfg)
    fetch = _model_list_fetcher()
    detail: list[str] = []
    cfg = upsert_aimds_defaults(cfg, fetch=fetch, lines=detail)
    enforced = _enforced_changes(before, cfg)
    if enforced:
        detail.insert(0, "enforced: " + ", ".join(enforced))

    if current >= _AIMDS_DEFAULTS_VERSION:
        changed = cfg != before
        head = (
            f"aimds-defaults: enforced policy v{_AIMDS_DEFAULTS_VERSION} (already current v{current})"
            if changed
            else f"aimds-defaults: already current (v{current})"
        )
        return cfg, changed, "\n".join([head, *detail])

    try:
        detail.extend(apply_one_shot_defaults(cfg, current, fetch=fetch))
    except _OneShotDeferred as exc:
        head = f"aimds-defaults: enforced policy v{_AIMDS_DEFAULTS_VERSION}; one-shot deferred ({exc})"
        return cfg, cfg != before, "\n".join([head, *detail])

    cfg[_AIMDS_DEFAULTS_VERSION_KEY] = _AIMDS_DEFAULTS_VERSION
    head = f"aimds-defaults: applied v{_AIMDS_DEFAULTS_VERSION} (from v{current})"
    return cfg, True, "\n".join([head, *dict.fromkeys(detail)])


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
