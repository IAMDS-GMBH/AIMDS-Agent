"""Deterministic part of the weekly Vault & Memory Curator (AIS-748).

The seeded curator job asked the model to find stray memory links, backup
directories, broken wikilinks and old ``_inbox`` entries itself. It had no
data, so it wrote Python through ``terminal`` (SUP-20261023-100653). The
mechanical steps run here instead, before the agent is built:

* memory links, ``HermesMemory.backup.*`` and loose project/user memories —
  the same routine every start runs (``_ensure_documents_memory_link``);
* ``_inbox`` entries older than seven days move to ``_inbox/_archive/``
  (moved, never deleted);
* the vault index is synced;
* broken wikilinks are listed.

Only the broken links need judgement. They reach the model as a
``## Collected Data`` block; with none, the run is silent and costs nothing.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEED_KEY = "vault-curator"
INBOX_DIR = "_inbox"
ARCHIVE_DIR = "_archive"
INBOX_MAX_AGE_DAYS = 7
#: Broken links listed for the model; the rest is counted.
MAX_LISTED_LINKS = 40
#: Folders whose notes are templates, archives or tooling, not content.
_SKIP_DIRS = {".obsidian", ".git", ".trash", "_templates", ARCHIVE_DIR, "node_modules"}

_WIKILINK_RE = re.compile(r"!?\[\[([^\]\|#\n]+)(?:[#|][^\]\n]*)?\]\]")
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


@dataclass
class CuratorReport:
    archived: List[str] = field(default_factory=list)
    broken_links: List[Tuple[str, str]] = field(default_factory=list)
    broken_total: int = 0
    notes_scanned: int = 0
    indexed: Optional[int] = None
    errors: List[str] = field(default_factory=list)

    @property
    def needs_agent(self) -> bool:
        return bool(self.broken_links)

    def summary(self) -> str:
        return (
            f"archived={len(self.archived)} broken_links={self.broken_total} "
            f"notes={self.notes_scanned} indexed={self.indexed if self.indexed is not None else '-'}"
            + (f" errors={len(self.errors)}" if self.errors else "")
        )


def is_curator_job(job: Dict[str, Any]) -> bool:
    """The seeded Vault & Memory Curator, recognised by its origin."""
    origin = job.get("origin") if isinstance(job, dict) else None
    if not isinstance(origin, dict) or origin.get("source") != "aimds-default-cron":
        return False
    return str(origin.get("seed_key") or "").strip().lower().replace("_", "-") == SEED_KEY


def _vault_dir() -> Path:
    from hermes_cli.config import _resolve_workspace_dir

    return Path(_resolve_workspace_dir())


def _unique_target(target_dir: Path, name: str) -> Path:
    candidate = target_dir / name
    if not candidate.exists():
        return candidate
    stem, suffix = os.path.splitext(name)
    n = 2
    while (target_dir / f"{stem}-{n}{suffix}").exists():
        n += 1
    return target_dir / f"{stem}-{n}{suffix}"


def archive_old_inbox(vault: Path, *, now: Optional[float] = None, max_age_days: int = INBOX_MAX_AGE_DAYS) -> List[str]:
    """Move ``_inbox`` entries older than *max_age_days* into ``_inbox/_archive/``."""
    inbox = vault / INBOX_DIR
    if not inbox.is_dir():
        return []
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    archive = inbox / ARCHIVE_DIR
    moved: List[str] = []
    for entry in sorted(inbox.iterdir()):
        if entry.name == ARCHIVE_DIR or entry.name.startswith("."):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            archive.mkdir(exist_ok=True)
            shutil.move(str(entry), str(_unique_target(archive, entry.name)))
            moved.append(entry.name)
        except OSError as exc:
            logger.warning("vault curator: could not archive %s: %s", entry, exc)
    return moved


def _notes(vault: Path) -> List[Path]:
    out: List[Path] = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        out.extend(Path(root) / f for f in files if f.endswith(".md"))
    return out


def _link_targets(vault: Path) -> Tuple[set, set]:
    """Lower-case names a wikilink may point at: note stems and file names, and vault-relative paths."""
    names: set = set()
    paths: set = set()
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in (".git", ".trash", "node_modules")]
        for f in files:
            rel = (Path(root) / f).relative_to(vault).as_posix().lower()
            names.add(f.lower())
            paths.add(rel)
            if f.endswith(".md"):
                names.add(f[:-3].lower())
                paths.add(rel[:-3])
    return names, paths


def broken_wikilinks(vault: Path) -> Tuple[List[Tuple[str, str]], int]:
    """``[(note, link)]`` for wikilinks that resolve to no file, and the number of notes scanned.

    Links inside code (fenced or inline) are examples, not links.
    """
    names, paths = _link_targets(vault)
    broken: List[Tuple[str, str]] = []
    notes = _notes(vault)
    for note in notes:
        try:
            text = note.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))
        rel_note = note.relative_to(vault).as_posix()
        seen: set = set()
        for raw in _WIKILINK_RE.findall(text):
            link = raw.strip()
            key = link.lower().lstrip("/")
            if not key or key in seen:
                continue
            seen.add(key)
            if key in paths or key.split("/")[-1] in names:
                continue
            broken.append((rel_note, link))
    return broken, len(notes)


def _sync_index(vault: Path) -> Optional[int]:
    from agent.memory_vault_index import VaultMetaIndex

    return VaultMetaIndex().sync_workspace_vault(vault)


def run(*, now: Optional[float] = None, vault: Optional[Path] = None) -> CuratorReport:
    """Do the mechanical maintenance and collect what is left for the model."""
    report = CuratorReport()
    vault = vault or _vault_dir()
    try:
        from hermes_cli.config import _ensure_documents_memory_link, get_hermes_home

        _ensure_documents_memory_link(Path(get_hermes_home()))
    except Exception as exc:
        report.errors.append(f"memory links: {exc}")
    if not vault.is_dir():
        report.errors.append(f"vault not found: {vault}")
        return report
    report.archived = archive_old_inbox(vault, now=now)
    broken, report.notes_scanned = broken_wikilinks(vault)
    report.broken_total = len(broken)
    report.broken_links = broken[:MAX_LISTED_LINKS]
    try:
        report.indexed = _sync_index(vault)
    except Exception as exc:
        report.errors.append(f"index: {exc}")
    return report


def render(report: CuratorReport) -> str:
    """The ``## Collected Data`` block for the model."""
    lines = ["## Collected Data", ""]
    done = [f"checked {report.notes_scanned} notes"]
    if report.archived:
        done.append(f"moved {len(report.archived)} _inbox entries older than {INBOX_MAX_AGE_DAYS} days to _inbox/_archive/")
    if report.indexed is not None:
        done.append("re-indexed the vault")
    lines.append("Already done by Hermes (do not repeat): " + "; ".join(done) + ".")
    lines.append("")
    lines.append(f"### Broken wikilinks ({report.broken_total})")
    lines.extend(f"- {note} → [[{link}]]" for note, link in report.broken_links)
    if report.broken_total > len(report.broken_links):
        lines.append(f"- … {report.broken_total - len(report.broken_links)} more (fix these first, the rest next week)")
    return "\n".join(lines)
