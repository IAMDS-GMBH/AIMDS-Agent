"""Configurable budget constants for tool result persistence.

Per-tool resolution: pinned > config overrides > registry > default.
"""

from dataclasses import dataclass, field
from typing import Dict

# Tools whose thresholds must never be overridden.
# read_file=inf prevents infinite persist->read->persist loops.
PINNED_THRESHOLDS: Dict[str, float] = {
    "read_file": float("inf"),
}

# Defaults matching the current hardcoded values in tool_result_storage.py.
# Kept here as the single source of truth; tool_result_storage.py imports these.
DEFAULT_RESULT_SIZE_CHARS: int = 100_000
DEFAULT_JIRA_RESULT_SIZE_CHARS: int = 25_000
DEFAULT_TURN_BUDGET_CHARS: int = 200_000
DEFAULT_PREVIEW_SIZE_CHARS: int = 1_500
# Once rows are auto-ingested into mcp_records the raw payload is redundant —
# a much lower persistence threshold applies (session 20260829_223307: a 99K
# worklog response sat just under the 100K default and was re-sent every turn).
DEFAULT_INGESTED_RESULT_SIZE_CHARS: int = 10_000
# Memory-server listings and searches are prose/JSON the shaper leaves alone
# (is_shapeable_tool == False), so a 20-40 KB catalogue went straight into
# the history (AIS-309, 14-day sample: memory_list ø 21 KB, memory_search
# 40 KB). Persist above this and keep the preview; memory_read and
# memory_context stay uncapped — those payloads are meant to be read whole.
DEFAULT_MEMORY_LISTING_RESULT_SIZE_CHARS: int = 12_000
_MEMORY_LISTING_SUFFIXES = ("memory_search", "memory_list")


@dataclass(frozen=True)
class BudgetConfig:
    """Immutable budget constants for the 3-layer tool result persistence system.

    Layer 2 (per-result): resolve_threshold(tool_name) -> threshold in chars.
    Layer 3 (per-turn):   turn_budget -> aggregate char budget across all tool
                          results in a single assistant turn.
    Preview:              preview_size -> inline snippet size after persistence.
    """

    default_result_size: int = DEFAULT_RESULT_SIZE_CHARS
    turn_budget: int = DEFAULT_TURN_BUDGET_CHARS
    preview_size: int = DEFAULT_PREVIEW_SIZE_CHARS
    ingested_result_size: int = DEFAULT_INGESTED_RESULT_SIZE_CHARS
    tool_overrides: Dict[str, int] = field(default_factory=dict)

    def resolve_threshold(self, tool_name: str) -> int | float:
        """Resolve the persistence threshold for a tool.

        Priority: pinned -> tool_overrides -> jira default -> memory listing
        default -> registry -> global default.
        """
        if tool_name in PINNED_THRESHOLDS:
            return PINNED_THRESHOLDS[tool_name]
        if tool_name in self.tool_overrides:
            return self.tool_overrides[tool_name]
        lowered = tool_name.lower()
        if "jira" in lowered:
            return DEFAULT_JIRA_RESULT_SIZE_CHARS
        if lowered.endswith(_MEMORY_LISTING_SUFFIXES):
            return DEFAULT_MEMORY_LISTING_RESULT_SIZE_CHARS
        from tools.registry import registry
        return registry.get_max_result_size(tool_name, default=self.default_result_size)


# Default config -- matches current hardcoded behavior exactly.
DEFAULT_BUDGET = BudgetConfig()
