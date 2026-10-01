"""Subagent definitions: lean, generic task types for ``delegate_task`` (AIS-456).

A definition is a Markdown file with YAML frontmatter. The body is a short
identity ("mini SOUL") that replaces the full system prompt of the child;
the frontmatter bounds what the child may use::

    ---
    name: digest
    description: Read many or large inputs in parts and return a structured extract.
    surfaces: [tui, cli]        # platforms that offer it (cli = developer posture)
    toolsets: [file, mcp]       # allowlist; ``mcp`` = the parent's MCP servers
    model: inherit              # inherit | fast (subagent model rule)
    max_iterations: 20
    max_result_chars: 4000      # result contract towards the parent
    writes: false               # read-only: Hermes' own write tools are removed
    ---
    <principles: role, way of working, result shape>

Definitions describe *kinds of work*, never domains or tool recipes: the
parent passes the domain in ``goal``/``context`` and the tools describe
themselves (see the "capabilities, not recipes" guidance).

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
from typing import Dict, List, Optional, Tuple

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

    @property
    def wants_mcp(self) -> bool:
        return MCP_TOOLSET_KEYWORD in self.toolsets

    @property
    def plain_toolsets(self) -> List[str]:
        return [t for t in self.toolsets if t != MCP_TOOLSET_KEYWORD]

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


__all__ = [
    "AgentDefinition",
    "BUNDLED_AGENTS_DIR",
    "MCP_TOOLSET_KEYWORD",
    "WRITE_TOOL_NAMES",
    "definition_dirs",
    "definitions_for",
    "get_definition",
    "load_definitions",
    "parse_definition",
]
