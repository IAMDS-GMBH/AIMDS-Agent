"""Anthropic prompt caching strategy.

Layout (Anthropic caches the prefix tools → system → messages, up to 4
breakpoints):

    last tool (prefix TTL) → system (prefix TTL) → last two markable messages (message TTL)

The prefix (tools + system prompt) is written once per session and reused
for the whole conversation, so it may carry the 1h tier; the conversation
breakpoints move every turn and stay on 5m. Anthropic requires longer TTLs
to precede shorter ones in the prompt — this layout satisfies that.

In the OpenAI-wire layout (OpenRouter, LiteLLM) ``tool`` messages carry no
marker: the breakpoints go to the newest ``user``/``assistant`` messages,
which still covers every earlier tool result.

Pure functions -- no class state, no AIAgent dependency.
"""

import copy
from typing import Any, Dict, List, Optional

VALID_TTLS = ("5m", "1h")

# The system prompt ends with a volatile timestamp block (calendar line,
# session id, model, provider — see agent/system_prompt.py's volatile tier).
# Splitting that tail OUT of the cached block is what makes the 1h prefix
# reusable across sessions and minutes (AIS-279): with the timestamp inside,
# the cache key changed every minute — two sessions 38s apart with a
# byte-identical 58KB prompt both paid the cache-write premium and read 0.
SYSTEM_VOLATILE_MARKER = "Current Local Time & Date: "


def split_system_for_caching(text: str) -> tuple:
    """(stable, volatile) split of a system prompt string.

    ``stable`` is everything up to the final timestamp block (cacheable);
    ``volatile`` is the timestamp block including its leading separator, so
    stable + volatile reproduces the input byte-for-byte. When no marker is
    found the whole text is stable and volatile is empty.
    """
    idx = text.rfind("\n\n" + SYSTEM_VOLATILE_MARKER)
    if idx <= 0:
        return text, ""
    return text[:idx], text[idx:]


def _apply_cache_marker(msg: dict, cache_marker: dict, native_anthropic: bool = False) -> None:
    """Add cache_control to a single message, handling all format variations."""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def _build_marker(ttl: str) -> Dict[str, str]:
    """Build a cache_control marker dict for the given TTL ('5m' or '1h')."""
    marker: Dict[str, str] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def mark_last_tool(tools: Optional[List[Dict[str, Any]]], ttl: str = "5m") -> Optional[List[Dict[str, Any]]]:
    """A copy of ``tools`` whose last entry carries a cache_control marker.

    Never mutates the caller's list: deferred tools are appended to
    ``agent.tools`` mid-session, and a marker left on the previous last tool
    would become a second breakpoint. OpenAI-format tools take the marker as
    a top-level key (what LiteLLM and the Anthropic adapter read); a marker
    already present is kept.
    """
    if not tools:
        return tools
    marked = list(tools)
    last = marked[-1]
    if isinstance(last, dict):
        if "cache_control" in last:
            return marked
        marked[-1] = {**last, "cache_control": _build_marker(ttl)}
    return marked


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: Optional[str] = None,
    native_anthropic: bool = False,
    max_breakpoints: int = 4,
    *,
    prefix_ttl: str = "5m",
    message_ttl: str = "5m",
) -> List[Dict[str, Any]]:
    """Apply caching strategy to messages for Anthropic models.

    Places up to ``max_breakpoints`` cache_control breakpoints: the system
    prompt (``prefix_ttl``) plus the newest markable non-system messages
    (``message_ttl``). ``cache_ttl`` is the legacy single-TTL form and sets
    both.

    Returns:
        Deep copy of messages with cache_control breakpoints injected.
    """
    if cache_ttl is not None:
        prefix_ttl = message_ttl = cache_ttl
    messages = copy.deepcopy(api_messages)
    if not messages or max_breakpoints <= 0:
        return messages

    breakpoints_used = 0

    if messages[0].get("role") == "system":
        marker = _build_marker(prefix_ttl)
        content = messages[0].get("content")
        volatile = ""
        if isinstance(content, str):
            stable, volatile = split_system_for_caching(content)
        if volatile:
            # Two text blocks: the breakpoint sits on the stable part so the
            # per-minute timestamp tail can no longer bust the prefix cache.
            # The Anthropic adapter and LiteLLM pass content-block system
            # messages through with cache_control intact.
            messages[0]["content"] = [
                {"type": "text", "text": stable, "cache_control": marker},
                {"type": "text", "text": volatile},
            ]
        else:
            _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = max_breakpoints - breakpoints_used
    if remaining > 0:
        message_marker = _build_marker(message_ttl)
        candidates = [
            i for i in range(len(messages))
            if messages[i].get("role") != "system"
            and (native_anthropic or messages[i].get("role") != "tool")
        ]
        for idx in candidates[-remaining:]:
            _apply_cache_marker(messages[idx], message_marker, native_anthropic=native_anthropic)

    return messages
