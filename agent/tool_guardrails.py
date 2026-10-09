"""Pure tool-call loop guardrail primitives.

The controller in this module is intentionally side-effect free: it tracks
per-turn tool-call observations and returns decisions. Runtime code owns whether
those decisions become warning guidance, synthetic tool results, or controlled
turn halts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from utils import safe_json_loads
from agent.tool_result_classification import file_mutation_result_landed


IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "mcp_filesystem_read_file",
        "mcp_filesystem_read_text_file",
        "mcp_filesystem_read_multiple_files",
        "mcp_filesystem_list_directory",
        "mcp_filesystem_list_directory_with_sizes",
        "mcp_filesystem_directory_tree",
        "mcp_filesystem_get_file_info",
        "mcp_filesystem_search_files",
    }
)

MUTATING_TOOL_NAMES = frozenset(
    {
        "terminal",
        "execute_code",
        "write_file",
        "patch",
        "todo",
        "memory",
        "skill_manage",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "send_message",
        "cronjob",
        "delegate_task",
        "process",
    }
)


# AIS-412: MCP tools are not in the static lists above, so their read loops
# went unnoticed (27 identical m365_list_emails calls, 21 paged
# list_work_packages calls in one turn). A tool counts as read-only when its
# name carries a read verb and no write verb.
_MCP_READ_VERBS = frozenset(
    {"list", "get", "search", "find", "read", "retrieve", "query", "describe", "fetch", "lookup", "show", "count"}
)
_MCP_WRITE_VERBS = frozenset(
    {
        "create", "update", "delete", "remove", "send", "add", "set", "move", "post", "write",
        "log", "book", "upload", "transition", "assign", "merge", "patch", "put", "reply",
        "forward", "respond", "cancel", "approve", "submit", "save", "manage", "import", "ingest",
        "rebuild", "toggle", "trash", "untrash", "edit", "rename", "copy", "init",
    }
)


_SEND_VERBS = frozenset({"send", "reply", "forward", "publish"})


def looks_like_send_tool(tool_name: str) -> bool:
    """Tools that deliver something to other people (mail, chat, posts)."""
    if not isinstance(tool_name, str):
        return False
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", tool_name)
    words = {w for w in re.split(r"[_\-.]+", spaced.lower()) if w}
    return bool(words & _SEND_VERBS)


def looks_like_read_only_mcp_tool(tool_name: str) -> bool:
    """True for ``mcp_*`` tools whose name reads as a pure lookup."""
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp_"):
        return False
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", tool_name[4:])
    words = {w for w in re.split(r"[_\-.]+", spaced.lower()) if w}
    return bool(words & _MCP_READ_VERBS) and not (words & _MCP_WRITE_VERBS)


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection.

    Warnings are enabled by default and never prevent tool execution. Hard stops
    are explicit opt-in so interactive CLI/TUI sessions get a gentle nudge unless
    the user enables circuit-breaker behavior in config.yaml.
    """

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    # AIS-412: the same read-only tool called this often in one turn with
    # changing arguments (paging, guessing filters) gets a strategy hint.
    same_tool_read_warn_after: int = 8
    # AIS-524: the same tool answering with the same error message, whatever
    # the arguments, is a blocker no retry can fix (a booking failed 5x with
    # "activity not found" while only the date changed, then the model
    # degenerated). On by default, independent of ``hard_stop_enabled``.
    same_error_halt_enabled: bool = True
    same_error_warn_after: int = 2
    same_error_halt_after: int = 3
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ToolCallGuardrailConfig":
        """Build config from the `tool_loop_guardrails` config.yaml section."""
        if not isinstance(data, Mapping):
            return cls()

        warn_after = data.get("warn_after")
        if not isinstance(warn_after, Mapping):
            warn_after = {}
        hard_stop_after = data.get("hard_stop_after")
        if not isinstance(hard_stop_after, Mapping):
            hard_stop_after = {}

        defaults = cls()
        return cls(
            warnings_enabled=_as_bool(data.get("warnings_enabled"), defaults.warnings_enabled),
            hard_stop_enabled=_as_bool(data.get("hard_stop_enabled"), defaults.hard_stop_enabled),
            same_error_halt_enabled=_as_bool(
                data.get("same_error_halt_enabled"), defaults.same_error_halt_enabled
            ),
            same_error_warn_after=_positive_int(
                warn_after.get("same_error", data.get("same_error_warn_after")),
                defaults.same_error_warn_after,
            ),
            same_error_halt_after=_positive_int(
                hard_stop_after.get("same_error", data.get("same_error_halt_after")),
                defaults.same_error_halt_after,
            ),
            exact_failure_warn_after=_positive_int(
                warn_after.get("exact_failure", data.get("exact_failure_warn_after")),
                defaults.exact_failure_warn_after,
            ),
            same_tool_failure_warn_after=_positive_int(
                warn_after.get("same_tool_failure", data.get("same_tool_failure_warn_after")),
                defaults.same_tool_failure_warn_after,
            ),
            no_progress_warn_after=_positive_int(
                warn_after.get("idempotent_no_progress", data.get("no_progress_warn_after")),
                defaults.no_progress_warn_after,
            ),
            exact_failure_block_after=_positive_int(
                hard_stop_after.get("exact_failure", data.get("exact_failure_block_after")),
                defaults.exact_failure_block_after,
            ),
            same_tool_failure_halt_after=_positive_int(
                hard_stop_after.get("same_tool_failure", data.get("same_tool_failure_halt_after")),
                defaults.same_tool_failure_halt_after,
            ),
            no_progress_block_after=_positive_int(
                hard_stop_after.get("idempotent_no_progress", data.get("no_progress_block_after")),
                defaults.no_progress_block_after,
            ),
            same_tool_read_warn_after=_positive_int(
                warn_after.get("same_tool_read", data.get("same_tool_read_warn_after")),
                defaults.same_tool_read_warn_after,
            ),
        )


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable, non-reversible identity for a tool name plus canonical args."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        canonical = canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        """Return public metadata without raw argument values."""
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """Decision returned by the tool-call guardrail controller."""

    action: str = "allow"  # allow | warn | block | halt
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None
    # AIS-524: the error a ``same_error_*`` decision is about (shortened).
    error_excerpt: str = ""

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    """Return sorted compact JSON for parsed tool arguments."""
    if not isinstance(args, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(args).__name__}")
    return json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Safety-fallback classifier used only when callers don't pass ``failed``.

    Mirrors ``agent.display._detect_tool_failure`` exactly so the guardrail
    never disagrees with the CLI's user-visible ``[error]`` tag. Production
    callers in ``run_agent.py`` always pass an explicit ``failed=`` derived
    from ``_detect_tool_failure``; this function exists so standalone callers
    (tests, tooling) still get consistent behavior.
    """
    if result is None:
        return False, ""
    if file_mutation_result_landed(tool_name, result):
        return False, ""

    if tool_name == "terminal":
        data = safe_json_loads(result)
        if isinstance(data, dict):
            exit_code = data.get("exit_code")
            if exit_code is not None and exit_code != 0:
                return True, f" [exit {exit_code}]"
        return False, ""

    if tool_name == "memory":
        data = safe_json_loads(result)
        if isinstance(data, dict):
            if data.get("success") is False and "exceed the limit" in data.get("error", ""):
                return True, " [full]"

    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"

    return False, ""


class ToolCallGuardrailController:
    """Per-turn controller for repeated failed/non-progressing tool calls."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None):
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._same_tool_read_counts: dict[str, int] = {}
        self._same_error_counts: dict[tuple[str, str], int] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {exact_count} "
                    "times with identical arguments. Stop retrying it unchanged; "
                    "change strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None:
                _result_hash, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        message=(
                            f"Blocked {tool_name}: this read-only call returned the same "
                            f"result {repeat_count} times. Stop repeating it unchanged; "
                            "use the result already provided or try a different query."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)

        if failed:
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._no_progress.pop(signature, None)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            # AIS-423: a failed send may still have been delivered (Graph
            # sendMail answered 202 and the tool misread it; two retries sent
            # the mail three times). Say so at the FIRST failure.
            if self.config.warnings_enabled and looks_like_send_tool(tool_name) and same_count == 1:
                return ToolGuardrailDecision(
                    action="warn",
                    code="send_failure_may_have_delivered",
                    message=(
                        f"{tool_name} reported a failure, but the message may already have been delivered. "
                        "Do not send it again automatically: verify first (e.g. sent items / the chat) or "
                        "tell the user and let them decide."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            same_error = self._same_error_decision(tool_name, result, signature)
            if same_error is not None and same_error.action == "halt":
                return same_error

            if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
                decision = ToolGuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"Stopped {tool_name}: it failed {same_count} times this turn. "
                        "Stop retrying the same failing tool path and choose a different approach."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )
                self._halt_decision = decision
                return decision

            if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    message=(
                        f"{tool_name} has failed {exact_count} times with identical arguments. "
                        "This looks like a loop; inspect the error and change strategy "
                        "instead of retrying it unchanged."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                )

            if same_error is not None:
                return same_error

            if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=_tool_failure_recovery_hint(tool_name, same_count),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)
        for key in [k for k in self._same_error_counts if k[0] == tool_name]:
            del self._same_error_counts[key]

        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. "
                    "The arguments are not changing what comes back — an argument the "
                    "tool ignores looks exactly like this. Check the tool's declared "
                    "parameters, use the result already provided, or change the query "
                    "instead of repeating it unchanged."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )

        read_count = self._same_tool_read_counts.get(tool_name, 0) + 1
        self._same_tool_read_counts[tool_name] = read_count
        threshold = self.config.same_tool_read_warn_after
        if self.config.warnings_enabled and read_count >= threshold and read_count % threshold == 0:
            return ToolGuardrailDecision(
                action="warn",
                code="same_tool_read_streak_warning",
                message=(
                    f"{tool_name} has run {read_count} times this turn with changing arguments. "
                    "Paging or guessing through results rarely converges: narrow the call with "
                    "the filters the tool declares, work with the results already returned, "
                    "or tell the user what is missing instead of calling it again."
                ),
                tool_name=tool_name,
                count=read_count,
                signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _same_error_decision(
        self,
        tool_name: str,
        result: str | None,
        signature: ToolCallSignature,
    ) -> ToolGuardrailDecision | None:
        """Warn, then halt, when a tool keeps returning the same error message."""
        if not self.config.same_error_halt_enabled or tool_name in _SAME_ERROR_EXEMPT_TOOLS:
            return None
        error = tool_error_message(result)
        if not error:
            return None
        key = (tool_name, _sha256(_normalize_error(error)))
        count = self._same_error_counts.get(key, 0) + 1
        self._same_error_counts[key] = count
        excerpt = _error_excerpt(error)
        if count >= self.config.same_error_halt_after:
            decision = ToolGuardrailDecision(
                action="halt",
                code="same_error_halt",
                message=(
                    f"Stopped {tool_name}: it returned the same error {count} times this turn "
                    f"(\"{excerpt}\"). Other arguments do not change it; report the blocker."
                ),
                tool_name=tool_name,
                count=count,
                signature=signature,
                error_excerpt=excerpt,
            )
            self._halt_decision = decision
            return decision
        if self.config.warnings_enabled and count >= self.config.same_error_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="same_error_warning",
                message=(
                    f"{tool_name} returned the same error {count} times: \"{excerpt}\". "
                    "Calling it again with other arguments will not change that. Do what the "
                    "error says (use the valid values or the lookup it names) or stop and tell "
                    "the user what blocks you. One more identical error ends this turn."
                ),
                tool_name=tool_name,
                count=count,
                signature=signature,
                error_excerpt=excerpt,
            )
        return None

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools or looks_like_read_only_mcp_tool(tool_name)


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    """Build a synthetic role=tool content string for a blocked tool call."""
    return json.dumps(
        {
            "error": decision.message,
            "guardrail": decision.to_metadata(),
        },
        ensure_ascii=False,
    )


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """Append runtime guidance to the current tool result content."""
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    suffix = (
        f"\n\n[{label}: "
        f"{decision.code}; count={decision.count}; {decision.message}]"
    )
    return (result or "") + suffix


def _tool_failure_recovery_hint(tool_name: str, count: int) -> str:
    """Action-oriented guidance for recovering from repeated tool failures."""
    common = (
        f"{tool_name} has failed {count} times this turn. This looks like a loop. "
        "Do not switch to text-only replies; keep using tools, but diagnose before retrying. "
        "First inspect the latest error/output and verify your assumptions. "
    )
    if tool_name == "terminal":
        return common + (
            "For terminal failures, run a small diagnostic such as `pwd && ls -la` "
            "in the same tool, then try an absolute path, a simpler command, a different "
            "working directory, or a different tool such as read_file/write_file/patch."
        )
    return common + (
        "Try different arguments, a narrower query/path, an absolute path when relevant, "
        "or a different tool that can make progress. If the blocker is external, report "
        "the blocker after one diagnostic attempt instead of repeating the same failing path."
    )


# Shell-style tools fail with free-form output; the same text there does not
# mean the same blocker (exploring with different commands is legitimate).
_SAME_ERROR_EXEMPT_TOOLS = frozenset({"terminal", "execute_code", "process"})

_ERROR_EXCERPT_CHARS = 240


def tool_error_message(result: str | None) -> str:
    """The explicit error message a tool result carries, or ``""``.

    Only structured errors count: an ``error`` field (string or object with a
    ``message``) or a plain-text result starting with ``Error``. Results that
    merely failed (non-zero exit code, empty output) carry no message.
    """
    if not result:
        return ""
    data = safe_json_loads(result)
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, Mapping):
            error = error.get("message") or error.get("detail")
        return error.strip() if isinstance(error, str) else ""
    text = result.strip()
    if text.startswith("Error"):
        # Guardrail suffixes appended to earlier results must not split one
        # error into several fingerprints.
        return text.split("\n\n[Tool loop", 1)[0].strip()
    return ""


# A clause that tells the model which function to call next
# ("— call pm_list_reference_data(kind=\"activities\") for valid names").
_DEV_HINT_RE = re.compile(r"\b[A-Za-z_][\w.]*\([^()]*\)")
_CLAUSE_SPLIT_RE = re.compile(r"\s+[—–]\s+|;\s+|(?<=[.!?])\s+")


def user_error_text(error: str) -> str:
    """A tool error as the end user should read it (AIS-524).

    Drops the clauses that only speak to the model (function-call hints such
    as ``call pm_list_reference_data(kind="activities")``) and trailing
    punctuation; the system's own description of the problem stays.
    """
    flat = re.sub(r"\s+", " ", error or "").strip()
    if flat.startswith("Error:"):
        flat = flat[len("Error:"):].strip()
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(flat) if c.strip()]
    kept = [c for c in clauses if not _DEV_HINT_RE.search(c)]
    text = " — ".join(kept) if kept else flat
    return text.rstrip(" .;—–")


def _normalize_error(error: str) -> str:
    """Fingerprint text: case, numbers and whitespace do not make a new error."""
    lowered = re.sub(r"\d+", "#", error.lower())
    return re.sub(r"\s+", " ", lowered).strip()[:500]


def _error_excerpt(error: str) -> str:
    flat = re.sub(r"\s+", " ", error).strip()
    if len(flat) <= _ERROR_EXCERPT_CHARS:
        return flat
    return flat[: _ERROR_EXCERPT_CHARS - 1].rstrip() + "…"


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _result_hash(result: str | None) -> str:
    parsed = safe_json_loads(result or "")
    if parsed is not None:
        try:
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except TypeError:
            canonical = str(parsed)
    else:
        canonical = result or ""
    return _sha256(canonical)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
