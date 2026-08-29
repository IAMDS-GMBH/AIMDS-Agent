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
* a managed file the user has edited is never overwritten — the new
  template text is placed next to it as ``<name>.template-new``;
* user content (findings, open questions, tasks, notes) is never touched;
* the version stamp is written last, so a partial run is retried.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes_cli.workspace_template")

VERSION_FILE = ".workspace-template-version"
_SKIP_NAMES = {".gitkeep", ".DS_Store"}
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
    return rel.parts[0] == "_templates"


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
    result: Dict[str, Any] = {"version": "", "added": [], "conflicts": [], "unchanged": 0, "skipped": []}
    if template is None or not root.is_dir():
        result["skipped"].append("no template or workspace")
        return result
    version = template_version(template)
    result["version"] = version
    if workspace_version(root) == version and (root / "HARNESS.md").exists():
        result["skipped"].append("already current")
        return result

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
            same = src.read_bytes() == dest.read_bytes()
        except OSError:
            same = True
        if same or not _is_managed(rel):
            result["unchanged"] += 1
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

    if not dry_run:
        try:
            (root / VERSION_FILE).write_text(version + "\n", encoding="utf-8")
        except OSError as exc:
            result["skipped"].append(f"version stamp: {exc}")
    if result["added"] or result["conflicts"]:
        logger.info(
            "workspace template %s: %d added, %d conflicts (.template-new) in %s",
            version, len(result["added"]), len(result["conflicts"]), root,
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
    for item in out["conflicts"]:
        print(f"  ! {item} (user copy kept; see {item}.template-new)")
    for item in out["skipped"]:
        print(f"  = {item}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
