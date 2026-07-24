"""Session-start bootstrap contract evaluation.

This module centralizes readiness checks for the mandatory first-turn loadout.
The contract is intentionally strict but behavior-safe:
- If required context is missing/stale, mark the turn as degraded.
- Always emit explicit reason codes so the loop never fails silently.
- Verify SOUL.md and AGENTS.md are present and readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
    soul_ok: bool = True
    agents_ok: bool = True


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


def check_soul_md(hermes_home: Optional[Path] = None) -> bool:
    """Check if SOUL.md is present and readable in HERMES_HOME.
    
    Returns True if SOUL.md exists and is readable, False otherwise.
    If hermes_home is None, tries to resolve it from environment.
    """
    if hermes_home is None:
        try:
            from hermes_constants import get_hermes_home
            hermes_home = get_hermes_home()
        except Exception:
            return False
    
    try:
        soul_path = hermes_home / "SOUL.md"
        if soul_path.exists() and soul_path.is_file():
            content = soul_path.read_text(encoding="utf-8").strip()
            return len(content) > 0
    except Exception:
        pass
    return False


def check_agents_md(cwd: Optional[Path] = None) -> bool:
    """Check if AGENTS.md is present and readable in CWD or workspace root.
    
    Returns True if AGENTS.md exists and is readable, False otherwise.
    If cwd is None, uses current working directory.
    """
    if cwd is None:
        cwd = Path.cwd()
    
    try:
        for name in ["AGENTS.md", "agents.md"]:
            candidate = cwd / name
            if candidate.exists() and candidate.is_file():
                content = candidate.read_text(encoding="utf-8").strip()
                return len(content) > 0
    except Exception:
        pass
    return False


def evaluate_session_bootstrap(
    *,
    payload: Optional[Dict[str, Any]],
    hydration_added: bool,
    memory_context_required: bool = True,
    hermes_home: Optional[Path] = None,
    cwd: Optional[Path] = None,
) -> BootstrapStatus:
    """Evaluate first-turn bootstrap readiness for the session.
    
    Checks:
    1. Memory context availability (if required)
    2. Hydration status
    3. SOUL.md presence in HERMES_HOME
    4. AGENTS.md presence in CWD
    
    Returns degraded status if any critical check fails, but continues with
    partial context rather than blocking (graceful degradation).
    """
    memory_context_ok = isinstance(payload, dict) and not bool(payload.get("error"))
    hydration_required = memory_context_requires_hydration(payload)
    hydration_ok = (not hydration_required) or hydration_added
    soul_ok = check_soul_md(hermes_home)
    agents_ok = check_agents_md(cwd)

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
            soul_ok=soul_ok,
            agents_ok=agents_ok,
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
            soul_ok=soul_ok,
            agents_ok=agents_ok,
        )
    
    # Warn if context files are missing, but don't block session
    if not soul_ok or not agents_ok:
        return BootstrapStatus(
            state="degraded",
            reason_code="context_files_missing",
            details=f"SOUL.md={'ok' if soul_ok else 'missing'}; AGENTS.md={'ok' if agents_ok else 'missing'}",
            ready=True,  # Still ready to continue, but degraded
            memory_context_required=memory_context_required,
            memory_context_ok=memory_context_ok,
            hydration_required=hydration_required,
            hydration_ok=hydration_ok,
            soul_ok=soul_ok,
            agents_ok=agents_ok,
        )
    
    return BootstrapStatus(
        state="ready",
        reason_code="ok",
        details="required session-start loadout blocks were initialized (SOUL.md, AGENTS.md, memory context)",
        ready=True,
        memory_context_required=memory_context_required,
        memory_context_ok=memory_context_ok,
        hydration_required=hydration_required,
        hydration_ok=hydration_ok,
        soul_ok=soul_ok,
        agents_ok=agents_ok,
    )


def build_bootstrap_status_block(status: BootstrapStatus) -> str:
    mem_state = "ok" if status.memory_context_ok else "missing"
    if status.hydration_required:
        hyd_state = "ok" if status.hydration_ok else "missing"
        hydration = f"hydration=required:{hyd_state}"
    else:
        hydration = "hydration=not-required"
    soul_state = "ok" if status.soul_ok else "missing"
    agents_state = "ok" if status.agents_ok else "missing"
    return (
        f"Session-start bootstrap status: {status.state} "
        f"({status.reason_code}; mem={mem_state}; {hydration}; soul={soul_state}; agents={agents_state})"
    )
