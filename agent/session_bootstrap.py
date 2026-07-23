"""Session-start bootstrap contract evaluation.

This module centralizes readiness checks for the mandatory first-turn loadout.
The contract is intentionally strict but behavior-safe:
- If required context is missing/stale, mark the turn as degraded.
- Always emit explicit reason codes so the loop never fails silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class BootstrapStatus:
    state: str  # ready | degraded
    reason_code: str
    details: str
    ready: bool
    memory_context_required: bool
    memory_context_ok: bool
    hydration_required: bool
    hydration_ok: bool


def memory_context_requires_hydration(payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return True

    candidate_maps: list[Dict[str, Any]] = [payload]
    nested = payload.get("result")
    if isinstance(nested, dict):
        candidate_maps.append(nested)

    stale_or_missing_flags = (
        "workspace_hydration_required",
        "session_start_workspace_hydration_required",
        "session_loadout_required",
        "context_missing",
        "context_stale",
        "stale_context",
        "memory_context_stale",
        "memory_context_missing",
    )
    missing_markers = {"missing", "stale", "required", "empty", "none"}

    for candidate in candidate_maps:
        if not isinstance(candidate, dict):
            continue
        for key in stale_or_missing_flags:
            value = candidate.get(key)
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in missing_markers:
                return True
    return False


def evaluate_session_bootstrap(
    *,
    payload: Optional[Dict[str, Any]],
    hydration_added: bool,
    memory_context_required: bool = True,
) -> BootstrapStatus:
    """Evaluate first-turn bootstrap readiness for the session."""
    memory_context_ok = isinstance(payload, dict) and not bool(payload.get("error"))
    hydration_required = memory_context_requires_hydration(payload)
    hydration_ok = (not hydration_required) or hydration_added

    if memory_context_required and not memory_context_ok:
        return BootstrapStatus(
            state="degraded",
            reason_code="memory_context_missing_or_failed",
            details="memory_context was unavailable or returned an error payload",
            ready=False,
            memory_context_required=True,
            memory_context_ok=False,
            hydration_required=hydration_required,
            hydration_ok=hydration_ok,
        )
    if not hydration_ok:
        return BootstrapStatus(
            state="degraded",
            reason_code="workspace_hydration_required_but_missing",
            details="memory_context reported stale/missing context but no local hydration block was added",
            ready=False,
            memory_context_required=memory_context_required,
            memory_context_ok=memory_context_ok,
            hydration_required=True,
            hydration_ok=False,
        )
    return BootstrapStatus(
        state="ready",
        reason_code="ok",
        details="required session-start loadout blocks were initialized",
        ready=True,
        memory_context_required=memory_context_required,
        memory_context_ok=memory_context_ok,
        hydration_required=hydration_required,
        hydration_ok=hydration_ok,
    )


def build_bootstrap_status_block(status: BootstrapStatus) -> str:
    return (
        "Session-start bootstrap status:\n"
        f"- state: {status.state}\n"
        f"- reason_code: {status.reason_code}\n"
        f"- memory_context_required: {str(status.memory_context_required).lower()}\n"
        f"- memory_context_ok: {str(status.memory_context_ok).lower()}\n"
        f"- workspace_hydration_required: {str(status.hydration_required).lower()}\n"
        f"- workspace_hydration_ok: {str(status.hydration_ok).lower()}\n"
        f"- details: {status.details}"
    )
