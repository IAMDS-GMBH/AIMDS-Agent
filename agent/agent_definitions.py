"""Subagent definitions: lean, generic task types for ``delegate_task`` (AIS-456).

A definition is a Markdown file with YAML frontmatter. The body is a short
identity ("mini SOUL") that replaces the full system prompt of the child;
the frontmatter bounds what the child may use::

    ---
    name: digest
    description: Read many or large inputs in parts and return a structured extract.
    when: large results         # AIS-796: the short "when" principle (prompt line)
    surfaces: [tui, cli]        # platforms that offer it (cli = developer posture)
    toolsets: [file, mcp]       # allowlist; ``mcp`` = the parent's MCP servers
    model: inherit              # inherit | fast (subagent model rule)
    max_iterations: 20
    max_result_chars: 4000      # result contract towards the parent
    writes: false               # read-only: Hermes' own write tools are removed
    allow_tools: [get, list]    # AIS-796: optional, words a tool call must carry
    deny_tools: [move]          # AIS-796: optional, words that block a tool call
    ---
    <principles: role, way of working, result shape>

Definitions describe *kinds of work*, never domains or tool recipes: the
parent passes the domain in ``goal``/``context`` and the tools describe
themselves (see the "capabilities, not recipes" guidance).

Tool rule (AIS-796). Every child that runs a definition passes each tool call
through :func:`tool_call_denial` (installed on its guardrail controller, so
both execution paths and the ``tool_call`` bridge see it):

* the words of the tool name and of an action-like argument (``action``,
  ``operation`` …) must not hit :data:`IRREVERSIBLE_TOOL_WORDS` — sending,
  deleting, confirming on the user's behalf — nor the definition's own
  ``deny_tools``. The floor is code, not frontmatter, so an override in
  ``~/.hermes/agents`` cannot lift it: a child cannot ask the user, so it
  never does what only the user may confirm;
* no argument may skip a tool's own confirmation contract (``confirm``/
  ``force`` set, ``dry_run`` turned off);
* with ``allow_tools`` the call must carry one of those words (MCP tools also
  through their action argument).

``writes: true`` with no toolset besides ``mcp`` writes through MCP tools only:
Hermes' own write tools are removed as for a read-only definition.

Lookup order: ``HERMES_HOME/agents/*.md`` (user overrides and own agents),
then the bundle shipped with the repo under
``installer/skills-hidden/aimds-loadout/agents/``. No installer step is
needed — like the SOUL variants, the bundle is read in place.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

BUNDLED_AGENTS_DIR = (
    Path(__file__).resolve().parent.parent / "installer" / "skills-hidden" / "aimds-loadout" / "agents"
)
#: Pseudo toolset in a definition: the parent's MCP servers.
MCP_TOOLSET_KEYWORD = "mcp"
#: Hermes' own tools that change files or state; removed for ``writes: false``.
WRITE_TOOL_NAMES = frozenset({"write_file", "patch", "skill_manage", "todo", "cronjob", "send_message"})
_VALID_MODEL_TIERS = ("inherit", "fast")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_DEFAULT_MAX_RESULT_CHARS = 4000
_MAX_BODY_CHARS = 4000
_MAX_WHEN_CHARS = 60

#: AIS-796: what no delegated child may do, whatever its definition says. A
#: child cannot ask the user, and these actions need the user's word: they
#: deliver something to other people, destroy or remove something, or confirm
#: on the user's behalf. Matched as whole words of the tool name and of an
#: action-like argument.
IRREVERSIBLE_TOOL_WORDS = frozenset({
    # delivers something to other people
    "send", "reply", "forward", "publish", "post", "share", "invite", "notify", "respond", "broadcast",
    # destroys or removes
    "delete", "remove", "trash", "purge", "destroy", "drop", "erase", "wipe", "truncate", "unlink",
    "revoke", "uninstall",
    # confirms, approves or runs something on the user's behalf; asks the user
    "confirm", "approve", "cancel", "execute", "exec", "clarify",
})
#: Arguments that carry the verb of a multi-action tool (``op_work_package``).
_ACTION_ARG_KEYS = ("action", "operation", "op", "method", "verb")
#: Argument words that, set, skip a tool's own confirmation step.
_CONFIRM_ARG_WORDS = frozenset({"confirm", "confirmed", "confirmation", "approve", "approved", "force", "yes"})
#: Argument names that, turned off, skip a tool's own dry run.
_DRY_RUN_ARG_NAMES = frozenset({"dry_run", "dryrun", "simulate", "validate_only"})
_FALSE_STRINGS = frozenset({"", "false", "no", "0", "off", "none", "null"})


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    body: str
    surfaces: Tuple[str, ...] = ("tui", "cli")
    toolsets: Tuple[str, ...] = ()
    model: str = "inherit"
    max_iterations: Optional[int] = None
    max_result_chars: int = _DEFAULT_MAX_RESULT_CHARS
    writes: bool = False
    source: str = ""
    digest: str = field(default="", compare=False)
    # AIS-796
    when: str = ""
    allow_tools: Tuple[str, ...] = ()
    deny_tools: Tuple[str, ...] = ()

    @property
    def wants_mcp(self) -> bool:
        return MCP_TOOLSET_KEYWORD in self.toolsets

    @property
    def plain_toolsets(self) -> List[str]:
        return [t for t in self.toolsets if t != MCP_TOOLSET_KEYWORD]

    @property
    def keeps_hermes_write_tools(self) -> bool:
        """Whether the child keeps Hermes' own write tools (file, todo …).

        ``writes: true`` with only ``mcp`` means MCP writes only (AIS-796)."""
        return self.writes and bool(self.plain_toolsets)

    def offered_on(self, platform: str) -> bool:
        return _surface_of(platform) in self.surfaces


def _surface_of(platform: str) -> str:
    """``cli`` is the developer posture; every other interactive surface
    (TUI, desktop, gateway chats) is the co-worker."""
    return "cli" if str(platform or "").strip().lower() == "cli" else "tui"


def _as_tuple(value) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v).strip().lower() for v in value if str(v).strip())


def _as_int(value, default: Optional[int]) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def parse_definition(text: str, source: str = "") -> Optional[AgentDefinition]:
    """One definition from file content, or None (logged) when unusable."""
    from agent.skill_utils import parse_frontmatter

    meta, body = parse_frontmatter(text or "")
    name = str(meta.get("name") or "").strip().lower()
    description = " ".join(str(meta.get("description") or "").split())
    body = (body or "").strip()
    if not _NAME_RE.match(name) or not description or not body:
        logger.warning("agent definition %s skipped: needs name, description and a body", source or "?")
        return None
    if len(body) > _MAX_BODY_CHARS:
        logger.warning("agent definition %s: body cut to %d chars (keep it lean)", source, _MAX_BODY_CHARS)
        body = body[:_MAX_BODY_CHARS]
    model = str(meta.get("model") or "inherit").strip().lower()
    if model not in _VALID_MODEL_TIERS:
        logger.warning("agent definition %s: unknown model tier %r, using inherit", source, model)
        model = "inherit"
    surfaces = tuple(s for s in _as_tuple(meta.get("surfaces")) if s in ("tui", "cli")) or ("tui", "cli")
    writes = meta.get("writes", False)
    if isinstance(writes, str):
        writes = writes.strip().lower() in ("true", "yes", "1", "on")
    when = " ".join(str(meta.get("when") or "").split())[:_MAX_WHEN_CHARS].strip()
    return AgentDefinition(
        name=name,
        description=description,
        body=body,
        surfaces=surfaces,
        toolsets=_as_tuple(meta.get("toolsets")),
        model=model,
        max_iterations=_as_int(meta.get("max_iterations"), None),
        max_result_chars=_as_int(meta.get("max_result_chars"), _DEFAULT_MAX_RESULT_CHARS) or _DEFAULT_MAX_RESULT_CHARS,
        writes=bool(writes),
        source=source,
        digest=hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12],
        when=when,
        allow_tools=_as_tuple(meta.get("allow_tools")),
        deny_tools=_as_tuple(meta.get("deny_tools")),
    )


def definition_dirs() -> List[Path]:
    dirs: List[Path] = []
    try:
        from hermes_constants import get_hermes_home

        dirs.append(get_hermes_home() / "agents")
    except Exception:
        pass
    dirs.append(BUNDLED_AGENTS_DIR)
    return dirs


_cache_lock = threading.Lock()
_cache: Dict[Tuple[Tuple[str, float], ...], Dict[str, AgentDefinition]] = {}


def _fingerprint(paths: List[Path]) -> Tuple[Tuple[str, float], ...]:
    out = []
    for path in paths:
        try:
            out.append((str(path), path.stat().st_mtime))
        except OSError:
            continue
    return tuple(out)


def load_definitions(dirs: Optional[List[Path]] = None) -> Dict[str, AgentDefinition]:
    """All definitions by name; an earlier directory wins on a name clash.

    Cached per file set + mtimes, so edits under ``~/.hermes/agents`` apply
    to the next ``delegate_task`` schema build without a restart.
    """
    files: List[Path] = []
    for directory in dirs if dirs is not None else definition_dirs():
        try:
            files.extend(sorted(p for p in Path(directory).glob("*.md") if p.is_file()))
        except OSError:
            continue
    key = _fingerprint(files)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    found: Dict[str, AgentDefinition] = {}
    for path in files:
        try:
            definition = parse_definition(path.read_text(encoding="utf-8"), source=str(path))
        except OSError as exc:
            logger.warning("agent definition %s unreadable: %s", path, exc)
            continue
        if definition is not None and definition.name not in found:
            found[definition.name] = definition
    with _cache_lock:
        _cache.clear()
        _cache[key] = found
    return found


def definitions_for(platform: str, dirs: Optional[List[Path]] = None) -> List[AgentDefinition]:
    """Definitions offered on *platform*, sorted by name."""
    return sorted(
        (d for d in load_definitions(dirs).values() if d.offered_on(platform)),
        key=lambda d: d.name,
    )


def get_definition(name: str, platform: str = "", dirs: Optional[List[Path]] = None) -> Optional[AgentDefinition]:
    definition = load_definitions(dirs).get(str(name or "").strip().lower())
    if definition is None or (platform and not definition.offered_on(platform)):
        return None
    return definition


# --------------------------------------------------------------------------- prompt line (AIS-796)


def delegation_line(platform: str, dirs: Optional[List[Path]] = None) -> str:
    """The one delegation sentence of the main system prompt, generated from
    the definitions offered on *platform*: kinds that share a ``when``
    principle are named together, kinds without one stay in the tool schema
    only. Empty when no definition carries a principle."""
    groups: Dict[str, List[str]] = {}
    for definition in definitions_for(platform, dirs):
        if definition.when:
            groups.setdefault(definition.when, []).append(definition.name)
    if not groups:
        return ""
    kinds = ", ".join(f"{'/'.join(names)} = {when}" for when, names in groups.items())
    return f"Delegate bulk work and keep only the summary: {kinds}."


# --------------------------------------------------------------------------- tool rule (AIS-796)


def _words(text: Any) -> set:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(text or ""))
    return {w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w}


def _bare_tool_name(tool_name: str) -> str:
    """``mcp_<server>_<tool>`` → ``<tool>`` (server words never allow a call)."""
    parts = tool_name.split("_", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "mcp" else tool_name


def _action_words(args: Mapping[str, Any]) -> set:
    words: set = set()
    for key in _ACTION_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            words |= _words(value)
    return words


def _is_set(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STRINGS
    return bool(value)


def _skipped_contract(args: Mapping[str, Any]) -> str:
    """The argument that would skip a tool's own confirmation, or ``""``."""
    for key, value in args.items():
        name = str(key).strip().lower().replace("-", "_")
        if name in _DRY_RUN_ARG_NAMES:
            if value is not None and not _is_set(value):
                return name
            continue
        if _words(name) & _CONFIRM_ARG_WORDS and _is_set(value):
            return name
    return ""


def tool_call_denial(definition: AgentDefinition, tool_name: str, args: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Why a child running *definition* must not make this call, or None.

    The message is written for the child: it names the rule and tells it to
    report the item as skipped instead of working around the block.
    """
    name = str(tool_name or "")
    call_args: Mapping[str, Any] = args if isinstance(args, Mapping) else {}
    if name == "tool_call":
        try:
            from tools.tool_search import resolve_underlying_call

            underlying, underlying_args, error = resolve_underlying_call(dict(call_args))
            if underlying and not error:
                name, call_args = underlying, underlying_args or {}
        except Exception:
            pass
    is_mcp = name.startswith("mcp_")
    action = _action_words(call_args)

    denied = (_words(name) | action) & (IRREVERSIBLE_TOOL_WORDS | set(definition.deny_tools))
    if denied:
        return (
            f"Blocked for the {definition.name} agent: {name} would {'/'.join(sorted(denied))} "
            "something, which only the user may confirm. Do not work around it: report the item "
            "as skipped with this reason and go on with the next one."
        )
    contract = _skipped_contract(call_args)
    if contract:
        return (
            f"Blocked for the {definition.name} agent: the argument '{contract}' would skip the "
            f"confirmation step of {name}. Leave it unconfirmed, report the item as skipped "
            "('needs confirmation') and go on with the next one."
        )
    if definition.allow_tools:
        allowed = _words(_bare_tool_name(name)) | (action if is_mcp else set())
        if not allowed & set(definition.allow_tools):
            return (
                f"Blocked for the {definition.name} agent: {name} is not one of the actions it may "
                f"take ({', '.join(definition.allow_tools)}). Report the item as skipped with this "
                "reason and go on with the next one."
            )
    return None


__all__ = [
    "AgentDefinition",
    "BUNDLED_AGENTS_DIR",
    "IRREVERSIBLE_TOOL_WORDS",
    "MCP_TOOLSET_KEYWORD",
    "WRITE_TOOL_NAMES",
    "definition_dirs",
    "definitions_for",
    "delegation_line",
    "get_definition",
    "load_definitions",
    "parse_definition",
    "tool_call_denial",
]
