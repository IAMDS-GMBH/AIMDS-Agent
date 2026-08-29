"""Where a mid-turn steer lands in the conversation.

The steer used to be appended to the *last tool message already in the
history* before the next API call. That rewrites a message that sits before
the prompt-cache breakpoints, so every steer threw the cached conversation
prefix away. The steer now becomes its own ``user`` message at the end of
the history — the cached prefix stays byte-identical, and the wrapper the
model was taught (STEER_CHANNEL_NOTE) is unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List


def inject_pre_api_steer(messages: List[Dict[str, Any]], steer_text: str) -> bool:
    """Append the steer as a user message. Returns False when there is no
    tool result yet (first iteration) — then it stays pending for the
    post-tool drain, exactly as before."""
    if not steer_text or not messages:
        return False
    if not any(isinstance(m, dict) and m.get("role") == "tool" for m in messages):
        return False
    from agent.prompt_builder import format_steer_marker

    messages.append({"role": "user", "content": format_steer_marker(steer_text).strip()})
    return True


__all__ = ["inject_pre_api_steer"]
