"""Generic, deterministic shaping of MCP tool results before they reach the model.

AIS-289. The result pipeline used to know only *size*: anything under the
persistence threshold (100k chars) went into the context verbatim, so Graph
payloads full of ``@odata`` keys, nulls and 60-entry drive listings were
re-sent every turn, and a FastMCP dict answer arrived twice (``content`` text
plus ``structuredContent``). Session ``20260904_090002_142f89``: the agent
lost the attachments of a Teams chat behind a 1,500-char preview and dug the
data out of SQLite by hand.

This module shapes JSON results of ``mcp_*`` tools:

* drops noise keys (``@odata.*``, ``etag``, ``changeKey``, ...) and empty
  values,
* caps item lists to ``max_items`` and, inside those lists, long strings to
  ``max_string_chars`` (rows are previews — a single object's long field such
  as an email body stays complete), flattening anything deeper than
  ``max_depth``,
* serialises with sorted keys and compact separators.

The output is a pure function of the input, so an identical answer produces a
byte-identical tool result — the prompt-cache prefix stays valid across turns.
The full rows live once in ``mcp_records`` (auto-ingest runs on the *unshaped*
payload first); a truncated list carries a ``_shaped`` block that points at
them by ``tool_use_id`` instead of repeating them.

Configuration (``mcp_results`` in config.yaml, all optional)::

    mcp_results:
      enabled: true
      max_items: 25
      max_string_chars: 1200
      max_depth: 6
      drop_keys: ["@odata.*", "etag", "changeKey", "@removed"]
      per_server:
        MSOffice365MCP: {max_items: 40}
      per_tool:
        mcp_TempoMCP_retrieveWorklogs: {max_items: 60}

Non-JSON results, non-``mcp_`` tools, non-data tools (memory, skills,
knowledge base, web, resources) and error payloads pass through untouched.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITEMS = 25
DEFAULT_MAX_STRING_CHARS = 1_200
DEFAULT_MAX_DEPTH = 6
DEFAULT_DROP_KEYS: Tuple[str, ...] = ("@odata.*", "etag", "changeKey", "@removed")

# Keys under which servers commonly return their item list. ``value`` is
# Microsoft Graph, ``matches`` is tool_search-style, the rest mirror
# ``tools.mcp_json_ingestor._extract_items`` so shaping and ingest agree on
# what "the rows" are.
ITEM_LIST_KEYS: Tuple[str, ...] = (
    "value", "items", "results", "records", "entries", "data", "values",
    "worklogs", "issues", "tickets", "cases", "matches", "candidates",
    "messages", "files", "attachments",
)

SHAPED_KEY = "_shaped"
TRUNCATED_MARK = "…[+{n} chars]"
DEPTH_MARK = "{…}"
_ROWS_HINT = "sql: SELECT raw_data FROM mcp_records WHERE tool_use_id='{tool_use_id}'"


@dataclass(frozen=True)
class ShapeConfig:
    enabled: bool = True
    max_items: int = DEFAULT_MAX_ITEMS
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS
    max_depth: int = DEFAULT_MAX_DEPTH
    drop_keys: Tuple[str, ...] = DEFAULT_DROP_KEYS
    per_server: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_tool: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def for_tool(self, tool_name: str) -> "ShapeConfig":
        """Resolve per-server / per-tool overrides (tool wins over server)."""
        overrides: Dict[str, Any] = {}
        server = _server_of(tool_name)
        for key, cfg in (self.per_server or {}).items():
            if server and str(key).lower() == server.lower() and isinstance(cfg, dict):
                overrides.update(cfg)
        for key, cfg in (self.per_tool or {}).items():
            if _tool_matches(tool_name, str(key)) and isinstance(cfg, dict):
                overrides.update(cfg)
        if not overrides:
            return self
        return _apply_overrides(self, overrides)


DEFAULT_SHAPE_CONFIG = ShapeConfig()


def _apply_overrides(base: ShapeConfig, overrides: Dict[str, Any]) -> ShapeConfig:
    kwargs: Dict[str, Any] = {}
    if "enabled" in overrides:
        kwargs["enabled"] = bool(overrides["enabled"])
    for name in ("max_items", "max_string_chars", "max_depth"):
        if name in overrides:
            try:
                kwargs[name] = max(0, int(overrides[name]))
            except (TypeError, ValueError):
                pass
    if "drop_keys" in overrides:
        raw = overrides["drop_keys"]
        if isinstance(raw, (list, tuple)):
            kwargs["drop_keys"] = tuple(str(k) for k in raw if str(k).strip())
    return replace(base, **kwargs) if kwargs else base


def load_shape_config() -> ShapeConfig:
    """``mcp_results`` from config.yaml on top of the defaults (fail-open)."""
    try:
        from hermes_cli.config import load_config

        raw = (load_config() or {}).get("mcp_results") or {}
    except Exception:
        return DEFAULT_SHAPE_CONFIG
    if not isinstance(raw, dict):
        return DEFAULT_SHAPE_CONFIG
    cfg = _apply_overrides(DEFAULT_SHAPE_CONFIG, raw)
    per_server = raw.get("per_server") if isinstance(raw.get("per_server"), dict) else {}
    per_tool = raw.get("per_tool") if isinstance(raw.get("per_tool"), dict) else {}
    return replace(cfg, per_server=dict(per_server), per_tool=dict(per_tool))


def is_shapeable_tool(tool_name: str) -> bool:
    """MCP data tools only. Memory, skill, knowledge-base, web and resource
    tools (``mcp_json_ingestor.should_ingest_tool`` == False) return prose or
    payloads the loop parses itself (onboarding, rules) — never reshape them."""
    if not tool_name or not str(tool_name).startswith("mcp_"):
        return False
    try:
        from tools.mcp_json_ingestor import should_ingest_tool

        return bool(should_ingest_tool(tool_name))
    except Exception:
        return True


def _server_of(tool_name: str) -> str:
    # mcp_<Server>_<tool>: the server component is the second segment.
    parts = str(tool_name or "").split("_", 2)
    return parts[1] if len(parts) >= 2 and parts[0] == "mcp" else ""


def _tool_matches(tool_name: str, pattern: str) -> bool:
    name = str(tool_name or "")
    if name == pattern or fnmatch.fnmatchcase(name, pattern):
        return True
    # Bare suffix (``m365_list_drive_files``) matches the prefixed name.
    return name.endswith(f"_{pattern}")


def _is_dropped_key(key: Any, patterns: Tuple[str, ...]) -> bool:
    if not isinstance(key, str):
        return False
    for pat in patterns:
        if key == pat or fnmatch.fnmatchcase(key, pat):
            return True
    return False


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _shape_string(text: str, cfg: ShapeConfig) -> str:
    limit = cfg.max_string_chars
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + TRUNCATED_MARK.format(n=len(text) - limit)


def _shape_value(
    value: Any, cfg: ShapeConfig, depth: int, stats: Dict[str, Any], *, in_list: bool = False
) -> Any:
    """Recursive shaping. Strings are capped only *inside item lists* (rows of
    a listing are previews; the full row is in mcp_records) — a single
    object's long field (an email body, a document text) stays complete and
    is left to the size-based persistence layer."""
    if isinstance(value, str):
        if in_list:
            capped = _shape_string(value, cfg)
            if capped != value:
                stats["strings_capped"] = stats.get("strings_capped", 0) + 1
            return capped
        return value
    if isinstance(value, dict):
        if depth >= cfg.max_depth:
            stats["flattened"] = stats.get("flattened", 0) + 1
            return DEPTH_MARK
        out: Dict[str, Any] = {}
        for key, val in value.items():
            if _is_dropped_key(key, cfg.drop_keys):
                stats["dropped_keys"] = stats.get("dropped_keys", 0) + 1
                continue
            if _is_empty(val):
                continue
            shaped = _shape_value(val, cfg, depth + 1, stats, in_list=in_list)
            if _is_empty(shaped):
                continue
            out[str(key)] = shaped
        return out
    if isinstance(value, list):
        if depth >= cfg.max_depth:
            stats["flattened"] = stats.get("flattened", 0) + 1
            return DEPTH_MARK
        items = [_shape_value(v, cfg, depth + 1, stats, in_list=True) for v in value]
        return [v for v in items if not _is_empty(v)]
    return value


def _cap_item_lists(data: Any, cfg: ShapeConfig, tool_use_id: str, rows_ingested: bool) -> Any:
    """Cap the item list(s) of a payload and attach the ``_shaped`` block."""
    if cfg.max_items <= 0:
        return data

    def _block(total: int, shown: int) -> Dict[str, Any]:
        block: Dict[str, Any] = {"total": total, "shown": shown, "truncated": True}
        if rows_ingested and tool_use_id:
            block["full_rows"] = _ROWS_HINT.format(tool_use_id=tool_use_id)
        return block

    if isinstance(data, list):
        if len(data) > cfg.max_items:
            return {"items": data[: cfg.max_items], SHAPED_KEY: _block(len(data), cfg.max_items)}
        return data

    if not isinstance(data, dict):
        return data

    # ``{"result": <payload>}`` is how the MCP handler wraps answers.
    if set(data.keys()) == {"result"} and isinstance(data.get("result"), (dict, list)):
        return {"result": _cap_item_lists(data["result"], cfg, tool_use_id, rows_ingested)}

    shaped_blocks: Dict[str, Dict[str, Any]] = {}
    out = dict(data)
    for key in ITEM_LIST_KEYS:
        val = out.get(key)
        if isinstance(val, list) and len(val) > cfg.max_items:
            out[key] = val[: cfg.max_items]
            shaped_blocks[key] = _block(len(val), cfg.max_items)
    if shaped_blocks:
        out[SHAPED_KEY] = shaped_blocks[next(iter(shaped_blocks))] if len(shaped_blocks) == 1 else shaped_blocks
        if len(shaped_blocks) == 1:
            out[SHAPED_KEY]["list"] = next(iter(shaped_blocks))
    return out


def _isolate_json(text: str) -> Optional[Any]:
    """Parse a tool result string as JSON, tolerating the untrusted wrapper."""
    body = (text or "").strip()
    if not body:
        return None
    if "<untrusted_tool_result" in body:
        start = body.find(">")
        end = body.rfind("</untrusted_tool_result>")
        if start != -1 and end != -1 and end > start:
            body = body[start + 1 : end].strip()
    if not body or body[0] not in "[{":
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _is_error_payload(data: Any) -> bool:
    return isinstance(data, dict) and ("error" in data or data.get("isError") is True)


def dumps_stable(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def shape_tool_result(
    content: str,
    tool_name: str,
    tool_use_id: str = "",
    *,
    rows_ingested: bool = False,
    config: Optional[ShapeConfig] = None,
) -> str:
    """Return the shaped result text, or ``content`` unchanged when shaping
    does not apply (non-MCP tool, non-JSON, error payload, disabled)."""
    if not isinstance(content, str) or not is_shapeable_tool(tool_name):
        return content
    cfg = (config or load_shape_config()).for_tool(tool_name)
    if not cfg.enabled:
        return content
    data = _isolate_json(content)
    if data is None or _is_error_payload(data):
        return content
    # Text answers wrapped as {"result": "<plain text>"} are not data.
    if isinstance(data, dict) and set(data.keys()) == {"result"} and isinstance(data["result"], str):
        nested = _isolate_json(data["result"])
        if nested is None:
            return content
        data = {"result": nested}

    stats: Dict[str, Any] = {}
    try:
        shaped = _shape_value(data, cfg, 0, stats)
        shaped = _cap_item_lists(shaped, cfg, tool_use_id, rows_ingested)
        text = dumps_stable(shaped)
    except Exception as exc:  # never break a tool call over shaping
        logger.debug("Result shaping failed for %s: %s", tool_name, exc)
        return content
    if stats or len(text) < len(content):
        logger.debug(
            "Shaped MCP result %s: %d -> %d chars (%s)",
            tool_name, len(content), len(text),
            ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "list caps only",
        )
    return text


__all__ = [
    "DEFAULT_SHAPE_CONFIG",
    "ITEM_LIST_KEYS",
    "SHAPED_KEY",
    "ShapeConfig",
    "dumps_stable",
    "is_shapeable_tool",
    "load_shape_config",
    "shape_tool_result",
]
