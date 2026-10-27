"""Bounded, deduplicated ``memory_context`` results (AIS-740).

The memory server's session briefing (standing rules, profile, hubs, recent
memories, suggested skills) arrived as ~28k chars per load — a JSON object
JSON-encoded a second time inside ``{"result": "..."}``, so every quote and
newline was escaped — and sat in the history for the rest of the session.
Session ``20261022_181315_fc3631`` loaded it twice more in a review fork.

This module keeps the payload's structure and every top-level field (the
onboarding flow and the bootstrap contract read them) and bounds the rest
deterministically:

* the doubly encoded ``result`` string is parsed once, so the model reads
  plain JSON instead of escaped text;
* hash keys (``content_hash``, ``hash``) go — the model never uses them;
* while the result is over budget, long strings inside item lists (rule
  bodies, memory previews, skill descriptions) are cut step by step, then
  the lists themselves; a ``_bounded`` note says how to read a full entry.

The output is a pure function of the input (sorted keys, compact
separators), so an identical briefing yields byte-identical text and the
prompt-cache prefix stays valid. A briefing identical to one already in the
conversation is replaced by a one-line reference
(:func:`dedupe_memory_context_message`).

Configuration: ``memory.context_max_chars`` (default 20000, 0 = off).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 20_000
#: Step-down ladder (cap for strings inside item lists, cap for list length).
#: Entry texts shrink first — a shortened rule still names itself (title,
#: slug) so the model can read it in full; only then do lists lose their
#: tail entries (servers list by priority).
_LADDER = (
    (1_200, None), (800, None), (600, None), (450, None), (350, None),
    (250, None), (250, 12), (200, 8), (160, 5),
)
_DROP_KEYS = frozenset({"content_hash", "hash"})
#: Fields the loop reads itself (onboarding flow) are never shaped.
_KEEP_PREFIXES = ("onboarding_",)
BOUNDED_KEY = "_bounded"
UNCHANGED_KEY = "_unchanged"
_BOUNDED_NOTE = (
    "Shortened to fit the prompt budget. Read a full rule, note or hub by its "
    "slug with the memory read tool when the preview is not enough."
)
_UNCHANGED_NOTE = (
    "Same memory context as loaded earlier in this conversation — it still applies."
)


def is_memory_context_tool(tool_name: Optional[str]) -> bool:
    name = str(tool_name or "")
    return name == "memory_context" or name.endswith("_memory_context")


def load_max_chars() -> int:
    """``memory.context_max_chars`` from config.yaml (fail-open to the default)."""
    try:
        from hermes_cli.config import load_config

        raw = ((load_config() or {}).get("memory") or {}).get("context_max_chars", DEFAULT_MAX_CHARS)
        return max(0, int(raw))
    except Exception:
        return DEFAULT_MAX_CHARS


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse(text: str) -> Optional[Any]:
    body = (text or "").strip()
    if not body or body[0] not in "[{":
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _drop_noise(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_noise(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, list):
        return [_drop_noise(v) for v in value]
    return value


def _keep(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(_KEEP_PREFIXES)


def _cap_strings(value: Any, cap: int, in_list: bool = False) -> Any:
    if isinstance(value, dict):
        return {k: (v if _keep(k) else _cap_strings(v, cap, in_list)) for k, v in value.items()}
    if isinstance(value, list):
        return [_cap_strings(v, cap, True) for v in value]
    if in_list and isinstance(value, str) and len(value) > cap:
        return f"{value[:cap]}…[+{len(value) - cap} chars]"
    return value


def _cap_lists(value: Any, cap: int, dropped: Dict[str, int], path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            k: (v if _keep(k) else _cap_lists(v, cap, dropped, f"{path}.{k}" if path else str(k)))
            for k, v in value.items()
        }
    if isinstance(value, list):
        if len(value) > cap:
            dropped[path or "list"] = dropped.get(path or "list", 0) + len(value) - cap
            value = value[:cap]
        return [_cap_lists(v, cap, dropped, path) for v in value]
    return value


def _body(data: Any) -> tuple[Any, Optional[str]]:
    """The briefing object and, when it sat in ``{"result": ...}``, that key."""
    if isinstance(data, dict) and isinstance(data.get("result"), (dict, str)):
        inner = data["result"]
        if isinstance(inner, str):
            inner = _parse(inner)
        if isinstance(inner, dict):
            return inner, "result"
    return data, None


def bound_memory_context_result(content: str, max_chars: Optional[int] = None) -> str:
    """Return *content* bounded to *max_chars* (see module docstring).

    Non-JSON results and error payloads pass through untouched.
    """
    if not isinstance(content, str):
        return content
    limit = load_max_chars() if max_chars is None else max_chars
    if limit <= 0:
        return content
    data = _parse(content)
    if not isinstance(data, dict) or "error" in data:
        return content
    body, wrapper_key = _body(data)
    if not isinstance(body, dict) or "error" in body:
        return content

    def _wrap(inner: Dict[str, Any]) -> str:
        if wrapper_key is None:
            return _dumps(inner)
        return _dumps({**data, wrapper_key: inner})

    body = _drop_noise(body)
    text = _wrap(body)
    if len(text) <= limit:
        return text if len(text) < len(content) else content

    dropped: Dict[str, int] = {}
    for string_cap, list_cap in _LADDER:
        dropped = {}
        shaped = _cap_strings(body, string_cap)
        if list_cap is not None:
            shaped = _cap_lists(shaped, list_cap, dropped)
        note: Any = _BOUNDED_NOTE
        if dropped:
            note = {"note": _BOUNDED_NOTE, "omitted_items": dict(sorted(dropped.items()))}
        text = _wrap({**shaped, BOUNDED_KEY: note})
        if len(text) <= limit:
            break
    logger.info(
        "memory_context result bounded: %d -> %d chars (limit %d%s)",
        len(content), len(text), limit,
        f", omitted {sum(dropped.values())} list items" if dropped else "",
    )
    return text


def _plain(content: Any) -> str:
    """Tool message text without the untrusted-data wrapper."""
    text = content if isinstance(content, str) else ""
    if "<untrusted_tool_result" in text:
        start = text.find(">")
        end = text.rfind("</untrusted_tool_result>")
        if start != -1 and end > start:
            text = text[start + 1:end]
            brace = text.find("{")
            text = text[brace:] if brace >= 0 else text
    return text.strip()


def is_unchanged_stub(content: Any) -> bool:
    data = _parse(_plain(content))
    return isinstance(data, dict) and data.get(UNCHANGED_KEY) is True


def dedupe_memory_context_message(messages: List[Dict[str, Any]]) -> bool:
    """Replace the newest tool message with a reference when it repeats a
    ``memory_context`` result already in *messages*. Returns True if replaced.

    Only the new message changes, so the cached prefix is untouched; the
    earlier, identical result stays the one the model reads.
    """
    if not messages:
        return False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "tool":
        return False
    name = str(last.get("name") or last.get("tool_name") or "")
    if not is_memory_context_tool(name):
        return False
    current = _plain(last.get("content"))
    if not current or is_unchanged_stub(current):
        return False
    data = _parse(current)
    if not isinstance(data, dict) or "error" in data:
        return False
    for earlier in messages[-2::-1]:
        if not isinstance(earlier, dict) or earlier.get("role") != "tool":
            continue
        if str(earlier.get("name") or earlier.get("tool_name") or "") != name:
            continue
        if _plain(earlier.get("content")) == current:
            last["content"] = _dumps({UNCHANGED_KEY: True, "note": _UNCHANGED_NOTE})
            logger.info("memory_context result repeats an earlier one in this conversation — replaced by a reference")
            return True
    return False


__all__ = [
    "BOUNDED_KEY",
    "DEFAULT_MAX_CHARS",
    "UNCHANGED_KEY",
    "bound_memory_context_result",
    "dedupe_memory_context_message",
    "is_memory_context_tool",
    "is_unchanged_stub",
    "load_max_chars",
]
