"""Deterministic delegation hint (AIS-796).

In one real session the main chat made 31 create, 19 update and 9 list calls
itself — 488 tool messages and 10 context summaries in 73 minutes — and
never handed a batch to a subagent. This counts, per turn, the calls of each
tool and the MCP results larger than the result shaper's list budget; past
the threshold the tool result gets one hint line.

The same pattern as ``skills.creation_nudge_interval``: a config number
(``delegation.nudge_threshold``, default 6, 0 switches it off), a counter,
no LLM call. Delegation is never forced — nothing is blocked, the model
reads one line. Each hint fires at most once per turn, and not at all once
the turn delegated.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

DEFAULT_NUDGE_THRESHOLD = 6

# Tools that organise the turn rather than do the work: repeating them is no
# batch a subagent could take over.
_HOUSEKEEPING_TOOLS = frozenset({
    "clarify", "todo", "memory", "tool_search", "tool_describe", "tool_call",
    "skill_view", "skills_list", "skill_manage", "session_search", "send_message",
})


def _looks_read_only(tool_name: str) -> bool:
    try:
        from agent.tool_guardrails import IDEMPOTENT_TOOL_NAMES, looks_like_read_only_mcp_tool

        return tool_name in IDEMPOTENT_TOOL_NAMES or looks_like_read_only_mcp_tool(tool_name)
    except Exception:
        return False


class DelegationNudge:
    """Per-agent counter; ``reset_for_turn`` at every user turn."""

    def __init__(self, threshold: int = DEFAULT_NUDGE_THRESHOLD, platform: str = "") -> None:
        try:
            self.threshold = max(0, int(threshold))
        except (TypeError, ValueError):
            self.threshold = DEFAULT_NUDGE_THRESHOLD
        self.platform = platform or ""
        self._kinds: Optional[Set[str]] = None
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._counts: Dict[str, int] = {}
        self._fired: Set[str] = set()
        self._delegated = False
        self._limits: Dict[str, float] = {}

    # ------------------------------------------------------------------ observe

    def observe(self, tool_name: str, result: Any) -> str:
        """The hint line for this call's result, or ``""``."""
        if self.threshold <= 0 or not tool_name:
            return ""
        if tool_name == "delegate_task":
            self._delegated = True
            return ""
        if self._delegated or tool_name in _HOUSEKEEPING_TOOLS:
            return ""
        count = self._counts.get(tool_name, 0) + 1
        self._counts[tool_name] = count
        if count > self.threshold and "similar" not in self._fired:
            self._fired.add("similar")
            return self._similar_hint(tool_name, count)
        size = len(result) if isinstance(result, str) else 0
        if size and "large" not in self._fired and size > self._large_limit(tool_name):
            self._fired.add("large")
            return self._large_hint(size)
        return ""

    # ------------------------------------------------------------------ hints

    def _offered(self) -> Set[str]:
        if self._kinds is None:
            try:
                from agent.agent_definitions import definitions_for

                self._kinds = {d.name for d in definitions_for(self.platform)}
            except Exception:
                self._kinds = set()
        return self._kinds

    def _via(self, *names: str) -> str:
        kinds = [n for n in names if n in self._offered()]
        return f" (agent {' or '.join(repr(k) for k in kinds)})" if kinds else ""

    def _similar_hint(self, tool_name: str, count: int) -> str:
        if _looks_read_only(tool_name):
            via, when = self._via("collect", "sweep"), ""
        else:
            via, when = self._via("apply"), " once the user approved the list"
        return (
            f"[Delegation hint: {count} {tool_name} calls this turn. One delegate_task run{via} "
            f"can take the remaining items{when}; you keep only its summary.]"
        )

    def _large_hint(self, size: int) -> str:
        return (
            f"[Delegation hint: this result has {size:,} characters. For more results like it, "
            f"a delegate_task run{self._via('digest')} reads them in parts and returns only the extract.]"
        )

    def _large_limit(self, tool_name: str) -> float:
        """The shaper's list budget for this MCP data tool; others never count."""
        if tool_name in self._limits:
            return self._limits[tool_name]
        limit: float = float("inf")
        try:
            from tools.mcp_result_shaper import is_shapeable_tool, load_shape_config

            if is_shapeable_tool(tool_name):
                budget = load_shape_config().for_tool(tool_name).list_byte_budget
                if budget > 0:
                    limit = budget
        except Exception:
            pass
        self._limits[tool_name] = limit
        return limit


__all__ = ["DEFAULT_NUDGE_THRESHOLD", "DelegationNudge"]
