"""Keep an installed workspace complete against the shipped template.

``installer/workspace-template/`` ships the Obsidian workspace skeleton:
``AGENTS.md`` (the operating manual), ``HARNESS.md``, ``_conventions.md``,
one ``_hub.md`` per folder, entity templates. The installer copied it with
``--ignore-existing`` once and never looked again, so a workspace created
from template v1 kept a v2 ``AGENTS.md`` (identical text) that told the
agent to "read the hub first" and "consult HARNESS.md" — files that did not
exist on that machine. Nothing read ``.workspace-template-version``.

``upgrade_workspace`` closes that gap non-destructively:

* files and folders the workspace lacks are added;
* an unedited copy of an earlier template version is replaced (backup under
  ``.archive/template-<old>/``); a managed file the user has edited is never
  overwritten — the new template text is placed next to it as
  ``<name>.template-new``;
* (v3) notes whose frontmatter is a JSON block or carries timestamps instead
  of ``YYYY-MM-DD`` are rewritten to the ``_conventions.md`` YAML schema once
  (backup under ``.archive/frontmatter-<old>/``); files without frontmatter
  are left alone;
* user content (findings, open questions, tasks, notes) is never touched;
* the version stamp is written last, so a partial run is retried.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes_cli.workspace_template")

VERSION_FILE = ".workspace-template-version"
PREVIOUS_VERSIONS_FILE = ".previous-versions.json"
_SKIP_NAMES = {".gitkeep", ".DS_Store", PREVIOUS_VERSIONS_FILE}
_BACKUP_DIR = ".archive"
# Files the template owns: an edited copy gets a .template-new sibling
# instead of being overwritten. Everything else that already exists is
# user content and is left alone.
_MANAGED_ROOT_FILES = {"AGENTS.md", "HARNESS.md", "README.md", "SETUP.md", "_conventions.md"}


def template_dir() -> Optional[Path]:
    """The shipped template: the checkout's installer/ dir, or the synced copy."""
    candidates = [
        Path(__file__).resolve().parents[1] / "installer" / "workspace-template",
    ]
    try:
        from hermes_constants import get_hermes_home

        candidates.append(get_hermes_home() / "tools" / "aimds-installer" / "workspace-template")
    except Exception:
        pass
    for candidate in candidates:
        if (candidate / VERSION_FILE).is_file():
            return candidate
    return None


def template_version(template: Optional[Path] = None) -> str:
    template = template or template_dir()
    if template is None:
        return ""
    try:
        return (template / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def workspace_version(root: Path) -> str:
    try:
        return (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_managed(rel: Path) -> bool:
    if len(rel.parts) == 1:
        return rel.name in _MANAGED_ROOT_FILES
    if rel.name == "_hub.md":
        return True
    return rel.parts[0] in ("_templates", ".obsidian")


def _previous_hashes(template: Path) -> Dict[str, set]:
    """Content hashes of managed files as shipped by earlier template versions."""
    try:
        data = json.loads((template / PREVIOUS_VERSIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, set] = {}
    for rel, versions in (data or {}).items():
        if isinstance(versions, dict):
            out[str(rel)] = {str(h) for h in versions.values()}
    return out


def _backup(root: Path, rel: Path, old_version: str, dry_run: bool) -> None:
    """Keep the previous copy — the vault never deletes."""
    if dry_run:
        return
    dest = root / _BACKUP_DIR / f"template-{old_version or 'v0'}" / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    except OSError as exc:
        logger.debug("backup of %s skipped: %s", rel, exc)


# ---------------------------------------------------------------------------
# Frontmatter normalisation (one-shot, v3): the old real-time dual-write left
# a JSON object between the --- markers of most vault files. Obsidian shows an
# empty Properties panel for those, the index read only JSON, and every new
# file followed one of two schemas. Everything becomes the _conventions.md
# YAML schema; the original is kept under .archive/frontmatter-<version>/.
# ---------------------------------------------------------------------------
_TYPE_MAP = {
    "notes": "note", "note": "note", "session": "journal", "journal": "journal", "rule": "knowledge",
    "reference": "knowledge", "knowledge": "knowledge", "tool": "knowledge", "profile": "note", "person": "contact",
    "contact": "contact", "decision": "decision", "project": "project", "hub": "hub", "meeting": "meeting",
    "document": "document", "idea": "idea", "security": "security", "task-list": "task-list", "tasks": "task-list",
    "task": "task-list", "automation": "automation", "archive": "archive", "report": "report",
    "conventions": "conventions", "morning-brief": "journal", "weekly-review": "journal", "digest": "journal",
}
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n?", re.S)


def _yaml_scalar(value: Any) -> str:
    text = str(value)
    if text == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`]|^\s|\s$|^(true|false|null|yes|no)$", text, re.I):
        return json.dumps(text, ensure_ascii=False)
    return text


def _render_frontmatter(meta: Dict[str, Any]) -> str:
    order = ["type", "title", "aliases", "created", "updated", "status", "projectStatus", "covers", "source",
             "related_to", "tags", "due", "due-reason", "purpose", "belongs-here", "does-not-belong"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    lines = ["---"]
    for key in keys:
        value = meta[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_yaml_scalar(v)}" for v in value)
        elif value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _date_from(value: Any, fallback: str) -> str:
    if isinstance(value, (int, float)) and value > 10_000_000:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
    text = str(value or "").strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else fallback


def _title_from_stem(stem: str) -> str:
    return re.sub(r"[-_]+", " ", stem).strip().capitalize()


def normalize_note(text: str, *, stem: str, mtime_date: str) -> Optional[str]:
    """The note with conventions-schema YAML frontmatter, or None when it is fine as is."""
    match = _FRONTMATTER_RE.match(text)
    body = text[match.end():] if match else text
    raw_meta = match.group(1).strip() if match else ""
    meta: Dict[str, Any]
    if raw_meta.startswith("{"):
        try:
            meta = json.loads(raw_meta)
        except ValueError:
            return None  # not ours to guess
        if not isinstance(meta, dict):
            return None
    elif match:
        try:
            import yaml

            loaded = yaml.safe_load(raw_meta)
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        meta = dict(loaded)
        # already YAML: only fix what breaks Obsidian's property types
        changed = False
        for key in ("created", "updated"):
            if key in meta and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(meta[key] or "")):
                meta[key] = _date_from(meta[key], mtime_date)
                changed = True
        if "type" in meta and str(meta["type"]) in _TYPE_MAP and _TYPE_MAP[str(meta["type"])] != str(meta["type"]):
            meta["type"] = _TYPE_MAP[str(meta["type"])]
            changed = True
        for key in ("created", "updated", "type", "title"):
            if key not in meta:
                changed = True
        if not changed:
            return None
        meta.setdefault("type", "note")
        meta.setdefault("title", _title_from_stem(stem))
        meta.setdefault("created", mtime_date)
        meta.setdefault("updated", meta.get("created", mtime_date))
        return _render_frontmatter(meta) + "\n" + body.lstrip("\n")
    else:
        return None  # no frontmatter at all: user content, not ours to decorate

    # JSON frontmatter: rebuild from what is there
    out: Dict[str, Any] = {}
    out["type"] = _TYPE_MAP.get(str(meta.get("type") or "note").strip().lower(), "note")
    out["title"] = str(meta.get("title") or "").strip() or _title_from_stem(stem)
    out["created"] = _date_from(meta.get("created") or meta.get("created_at"), _date_from(meta.get("updated_at"), mtime_date))
    out["updated"] = _date_from(meta.get("updated") or meta.get("updated_at"), mtime_date)
    tags = meta.get("tags")
    if isinstance(tags, list) and tags:
        out["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    for key in ("status", "related_to", "due", "aliases"):
        if meta.get(key) not in (None, "", []):
            out[key] = meta[key]
    if meta.get("slug"):
        out["source"] = f"memory:{meta['slug']}"
    body = body.lstrip("\n")
    if body.startswith("{") and body.rstrip().endswith("}"):
        body = "## Raw\n\n```json\n" + body.rstrip() + "\n```\n"
    return _render_frontmatter(out) + "\n" + body


def _template_hashes(template: Optional[Path]) -> set:
    if template is None:
        return set()
    out = set()
    for src in template.rglob("*.md"):
        try:
            out.add(hashlib.md5(src.read_bytes()).hexdigest())
        except OSError:
            continue
    return out


def normalize_frontmatter(
    root: Path, *, dry_run: bool = False, backup_version: str = "v2", template: Optional[Path] = None
) -> List[str]:
    """Rewrite every note whose frontmatter is not conventions-schema YAML.

    Files byte-identical to the shipped template (hubs, conventions) are the
    template's business and are left alone."""
    changed: List[str] = []
    shipped = _template_hashes(template)
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if rel.parts and (rel.parts[0].startswith(".") or rel.parts[0] == "_templates"):
            continue
        try:
            raw_bytes = path.read_bytes()
            if hashlib.md5(raw_bytes).hexdigest() in shipped:
                continue
            text = raw_bytes.decode("utf-8")
            mtime_date = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, UnicodeDecodeError):
            continue
        new_text = normalize_note(text, stem=path.stem, mtime_date=mtime_date)
        if new_text is None or new_text == text:
            continue
        changed.append(str(rel))
        if dry_run:
            continue
        backup = root / _BACKUP_DIR / f"frontmatter-{backup_version}" / rel
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            logger.debug("normalise %s skipped: %s", rel, exc)
            changed.pop()
    return changed


def upgrade_workspace(
    root: Path,
    *,
    template: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add what the workspace lacks; never overwrite; stamp the version.

    Returns ``{"version", "added", "conflicts", "unchanged", "skipped"}``
    where ``conflicts`` lists managed files whose user copy differs from the
    template (a ``.template-new`` sibling was written).
    """
    root = Path(root).expanduser()
    template = template or template_dir()
    result: Dict[str, Any] = {"version": "", "added": [], "replaced": [], "conflicts": [], "normalized": [], "unchanged": 0, "skipped": []}
    if template is None or not root.is_dir():
        result["skipped"].append("no template or workspace")
        return result
    version = template_version(template)
    result["version"] = version
    old_version = workspace_version(root)
    if old_version == version and (root / "HARNESS.md").exists():
        result["skipped"].append("already current")
        return result
    previous = _previous_hashes(template)

    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        if rel.name in _SKIP_NAMES or rel.name == VERSION_FILE:
            continue
        dest = root / rel
        if src.is_dir():
            if not dest.exists():
                result["added"].append(str(rel) + "/")
                if not dry_run:
                    dest.mkdir(parents=True, exist_ok=True)
            continue
        if not dest.exists():
            result["added"].append(str(rel))
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            continue
        try:
            dest_bytes = dest.read_bytes()
            same = src.read_bytes() == dest_bytes
        except OSError:
            same = True
            dest_bytes = b""
        if same or not _is_managed(rel):
            result["unchanged"] += 1
            continue
        # An unedited copy of an earlier template version is ours to replace
        # (backup kept); anything else is the user's and gets a sibling.
        if hashlib.md5(dest_bytes).hexdigest() in previous.get(str(rel), set()):
            result["replaced"].append(str(rel))
            if not dry_run:
                _backup(root, rel, old_version, dry_run)
                shutil.copy2(src, dest)
            continue
        sibling = dest.with_name(dest.name + ".template-new")
        try:
            if sibling.exists() and sibling.read_bytes() == src.read_bytes():
                result["unchanged"] += 1
                continue
        except OSError:
            pass
        result["conflicts"].append(str(rel))
        if not dry_run:
            shutil.copy2(src, sibling)

    # v3: one-shot frontmatter normalisation of the whole vault (idempotent).
    if version >= "v3" and old_version < "v3":
        result["normalized"] = normalize_frontmatter(root, dry_run=dry_run, backup_version=old_version or "v0", template=template)

    if not dry_run:
        try:
            (root / VERSION_FILE).write_text(version + "\n", encoding="utf-8")
        except OSError as exc:
            result["skipped"].append(f"version stamp: {exc}")
    if result["added"] or result["conflicts"] or result["replaced"] or result["normalized"]:
        logger.info(
            "workspace template %s: %d added, %d replaced, %d conflicts (.template-new), %d notes normalised in %s",
            version, len(result["added"]), len(result["replaced"]), len(result["conflicts"]), len(result["normalized"]), root,
        )
    return result


def upgrade_configured_workspace() -> Optional[Dict[str, Any]]:
    """Best-effort upgrade of the configured workspace (gateway/desktop start)."""
    try:
        from hermes_cli.config import _resolve_workspace_dir

        root = _resolve_workspace_dir()
    except Exception:
        return None
    if not root.is_dir() or not (root / "AGENTS.md").exists():
        return None  # never seeded — the installer owns first creation
    try:
        return upgrade_workspace(root)
    except Exception as exc:
        logger.debug("workspace upgrade skipped: %s", exc)
        return None


def _main(argv: List[str]) -> int:
    dry = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if paths:
        root = Path(paths[0])
    else:
        from hermes_cli.config import _resolve_workspace_dir

        root = _resolve_workspace_dir()
    out = upgrade_workspace(root, dry_run=dry)
    print(f"workspace template {out['version'] or '?'} → {root}")
    for item in out["added"]:
        print(f"  + {item}")
    for item in out.get("replaced", []):
        print(f"  ↑ {item} (unedited older template replaced; backup in .archive/)")
    for item in out["conflicts"]:
        print(f"  ! {item} (user copy kept; see {item}.template-new)")
    if out.get("normalized"):
        print(f"  ~ {len(out['normalized'])} notes: frontmatter normalised to YAML (backups in .archive/frontmatter-*/)")
    for item in out["skipped"]:
        print(f"  = {item}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
