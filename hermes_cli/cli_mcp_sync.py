"""Put the AIMDSSuiteMCP entry into other local CLIs' MCP config (AIS-404).

Hermes only ever shows the Suite API key masked, so nobody can wire the Suite
MCP into Claude Code, the Gemini CLI or the Copilot CLI by hand. The entry then
either never appears there or quietly rots when the key is rotated. This module
reports, per CLI, whether it is installed and whether its configuration matches
the Suite, and writes the entry on request.

Shaped after :mod:`hermes_cli.codex_runtime_plugin_migration`, which already
does this for Codex: a report object describing what happened, and writes that
never disturb anything the user put there themselves.

Two properties matter more than the rest:

* **The key never leaves Python.** ``tools.mcp_tool._load_mcp_config()`` hands
  back the entry with ``${IAMDS_LITELLM_API_KEY}`` already interpolated, so the
  real token is available server-side and the HTTP layer only ever returns a
  status report. Drift is compared by SHA-256 fingerprint, never by value, so
  no log line or API response can leak it.
* **Foreign files are edited, not rewritten.** ``~/.claude.json`` carries tens
  of kilobytes of unrelated Claude Code state next to ``mcpServers``; only that
  one key is touched, the file's own indentation is kept, a backup is written
  first and the replacement is atomic.

The key is written literally because none of these CLIs understand an env
indirection. That is a deliberate product decision (2026-09-22) and the reason
the UI names the file it lands in before writing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = __import__("logging").getLogger(__name__)

#: Name of the server entry in every config we touch — the same one Hermes uses.
SUITE_SERVER_NAME = "AIMDSSuiteMCP"

_BACKUP_SUFFIX = ".hermes-backup"
_INDENT_RE = re.compile(r"^\{\s*\n(\s+)\S", re.MULTILINE)


@dataclass(frozen=True)
class CliTarget:
    """A CLI whose MCP config Hermes can write.

    All three currently supported CLIs happen to share the shape
    ``{"mcpServers": {"<name>": {"type": "http", "url": …, "headers": …}}}``,
    which is why one writer covers them. Claude *Desktop* deliberately is not
    here: it drives remote servers through the ``mcp-remote`` stdio bridge with
    an ``${AUTH_HEADER}`` indirection, a different idiom that would need its own
    translation.
    """

    id: str
    label: str
    binary: str
    #: Path of the config file relative to the user's home directory.
    relative_config: Tuple[str, ...]

    @property
    def config_path(self) -> Path:
        return Path.home().joinpath(*self.relative_config)


CLI_TARGETS: Tuple[CliTarget, ...] = (
    CliTarget("claude", "Claude Code", "claude", (".claude.json",)),
    CliTarget("gemini", "Gemini CLI", "gemini", (".gemini", "settings.json")),
    CliTarget("copilot", "GitHub Copilot CLI", "copilot", (".copilot", "mcp-config.json")),
)

# State names the UI switches on. "unknown" covers a config we cannot read at
# all (malformed JSON, no permission) — claiming "not configured" there would
# invite a write that silently destroys whatever is in the file.
STATE_NOT_INSTALLED = "not_installed"
STATE_NOT_CONFIGURED = "not_configured"
STATE_IN_SYNC = "in_sync"
STATE_URL_DRIFT = "url_drift"
STATE_KEY_DRIFT = "key_drift"
STATE_UNKNOWN = "unknown"


@dataclass
class CliTargetStatus:
    id: str
    label: str
    installed: bool
    config_path: str
    config_exists: bool
    entry_present: bool
    state: str
    url: str = ""
    url_matches: Optional[bool] = None
    key_matches: Optional[bool] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CliSyncReport:
    """What :func:`apply_to_target` did, in the shape the card renders."""

    id: str
    ok: bool = False
    #: "written" | "unchanged" | "needs_confirmation" | "error"
    outcome: str = "error"
    config_path: str = ""
    backup_path: str = ""
    created_config: bool = False
    replaced_key: bool = False
    error: str = ""
    status: Optional[Dict[str, Any]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fingerprint(value: str) -> str:
    """Short digest of a secret, for comparing without ever handling the value."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12] if value else ""


def _norm_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def find_cli(target: CliTarget) -> Optional[str]:
    """Path of ``target``'s executable, or None.

    Plain :func:`shutil.which` on purpose: the hardened resolver in
    ``hermes_constants`` exists for Node shims Hermes spawns itself, and we only
    ask whether the user has the CLI, never run it.
    """
    try:
        return shutil.which(target.binary)
    except Exception:
        return None


def suite_mcp_entry() -> Optional[Dict[str, Any]]:
    """The AIMDSSuiteMCP entry as the foreign CLIs need it, or None.

    Values come from Hermes' own resolved MCP config, so the Authorization
    header is the interpolated one — the literal bearer token these CLIs
    require.
    """
    try:
        from tools.mcp_tool import _load_mcp_config

        configured = _load_mcp_config() or {}
    except Exception as exc:
        logger.debug("could not read the Hermes MCP config: %s", exc)
        return None

    entry = configured.get(SUITE_SERVER_NAME) or {}
    url = str(entry.get("url") or "").strip()
    headers = entry.get("headers") or {}
    auth = ""
    if isinstance(headers, dict):
        auth = next((str(v) for k, v in headers.items() if str(k).lower() == "authorization"), "")
    if not url or not auth.strip():
        return None
    # An uninterpolated placeholder means the env var behind it is missing;
    # writing "Bearer ${IAMDS_LITELLM_API_KEY}" into a foreign CLI would produce
    # a config that looks right and fails on every call.
    if "${" in auth:
        logger.debug("suite MCP authorization is still a placeholder — no key to hand over")
        return None
    return {"type": "http", "url": url, "headers": {"Authorization": auth}}


def _read_config(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """Parse ``path``. Returns ``(data, error)``; ``({}, "")`` when absent."""
    if not path.is_file():
        return {}, ""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {path.name}: {exc}"
    if not raw.strip():
        return {}, ""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"{path.name} is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} does not contain a JSON object"
    return data, ""


def status_for(target: CliTarget, entry: Optional[Dict[str, Any]] = None) -> CliTargetStatus:
    """Whether ``target`` is installed and how its entry compares to the Suite."""
    if entry is None:
        entry = suite_mcp_entry()

    binary = find_cli(target)
    path = target.config_path
    result = CliTargetStatus(
        id=target.id,
        label=target.label,
        installed=bool(binary),
        config_path=str(path),
        config_exists=path.is_file(),
        entry_present=False,
        state=STATE_NOT_INSTALLED if not binary else STATE_NOT_CONFIGURED,
    )
    if not binary:
        return result

    data, error = _read_config(path)
    if data is None:
        result.state = STATE_UNKNOWN
        result.error = error
        return result

    servers = data.get("mcpServers")
    current = servers.get(SUITE_SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(current, dict):
        return result

    result.entry_present = True
    result.url = str(current.get("url") or "")
    headers = current.get("headers") or {}
    current_auth = ""
    if isinstance(headers, dict):
        current_auth = next(
            (str(v) for k, v in headers.items() if str(k).lower() == "authorization"), ""
        )

    if entry is None:
        # Nothing to compare against — say so instead of guessing "in sync".
        result.state = STATE_UNKNOWN
        result.error = "no AIMDS-Suite key configured in Hermes"
        return result

    # Trailing slashes differ between how the URL is built and how it is stored;
    # comparing raw would report a mismatch that is not one.
    result.url_matches = _norm_url(result.url) == _norm_url(entry["url"])
    result.key_matches = _fingerprint(current_auth) == _fingerprint(entry["headers"]["Authorization"])

    if not result.url_matches:
        result.state = STATE_URL_DRIFT
    elif not result.key_matches:
        result.state = STATE_KEY_DRIFT
    else:
        result.state = STATE_IN_SYNC
    return result


def all_statuses() -> Dict[str, Any]:
    entry = suite_mcp_entry()
    return {
        "suite_configured": entry is not None,
        "targets": [status_for(target, entry).to_dict() for target in CLI_TARGETS],
    }


def _detect_indent(raw: str) -> Any:
    """The file's own indentation, so rewriting it does not reflow everything."""
    match = _INDENT_RE.search(raw)
    if not match:
        return 2
    indent = match.group(1)
    return "\t" if indent.startswith("\t") else len(indent)


def _write_config(path: Path, data: Dict[str, Any], indent: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hermes-tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def apply_to_target(target_id: str, *, replace_key: bool = False) -> CliSyncReport:
    """Write the Suite entry into ``target_id``'s config.

    Refuses to overwrite an existing entry whose key differs unless
    ``replace_key`` is set: that key may be one the user put there on purpose,
    and replacing a working credential unasked is not ours to do.
    """
    target = next((t for t in CLI_TARGETS if t.id == target_id), None)
    if target is None:
        return CliSyncReport(id=target_id, error=f"unknown CLI {target_id!r}")

    entry = suite_mcp_entry()
    if entry is None:
        return CliSyncReport(
            id=target.id,
            config_path=str(target.config_path),
            error="no usable AIMDS-Suite MCP configuration in Hermes",
        )

    before = status_for(target, entry)
    report = CliSyncReport(id=target.id, config_path=str(target.config_path))

    if not before.installed:
        report.error = f"{target.label} is not installed"
        return report
    if before.state == STATE_UNKNOWN:
        report.error = before.error or "configuration cannot be read"
        return report
    if before.state == STATE_IN_SYNC:
        report.ok = True
        report.outcome = "unchanged"
        report.status = before.to_dict()
        return report
    if before.state == STATE_KEY_DRIFT and not replace_key:
        report.outcome = "needs_confirmation"
        report.status = before.to_dict()
        return report

    path = target.config_path
    raw = ""
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            report.error = f"cannot read {path.name}: {exc}"
            return report

    data, error = _read_config(path)
    if data is None:
        report.error = error
        return report

    if raw.strip():
        backup = path.with_name(path.name + _BACKUP_SUFFIX)
        try:
            backup.write_text(raw, encoding="utf-8")
            report.backup_path = str(backup)
        except OSError as exc:
            # Refuse rather than edit someone's config with no way back.
            report.error = f"could not write a backup next to {path.name}: {exc}"
            return report
    else:
        report.created_config = True

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[SUITE_SERVER_NAME] = dict(entry)
    data["mcpServers"] = servers

    try:
        _write_config(path, data, _detect_indent(raw))
    except OSError as exc:
        report.error = f"could not write {path.name}: {exc}"
        return report

    report.ok = True
    report.outcome = "written"
    report.replaced_key = before.state == STATE_KEY_DRIFT
    report.status = status_for(target, entry).to_dict()
    logger.info(
        "AIMDSSuiteMCP written to %s (%s)", path, "key replaced" if report.replaced_key else "new entry"
    )
    return report


__all__ = [
    "CLI_TARGETS",
    "CliSyncReport",
    "CliTarget",
    "CliTargetStatus",
    "STATE_IN_SYNC",
    "STATE_KEY_DRIFT",
    "STATE_NOT_CONFIGURED",
    "STATE_NOT_INSTALLED",
    "STATE_UNKNOWN",
    "STATE_URL_DRIFT",
    "SUITE_SERVER_NAME",
    "all_statuses",
    "apply_to_target",
    "find_cli",
    "status_for",
    "suite_mcp_entry",
]
