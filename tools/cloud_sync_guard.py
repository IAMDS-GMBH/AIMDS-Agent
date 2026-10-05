"""Keep content searches from downloading cloud-only files (OneDrive on Windows).

OneDrive "Files On-Demand" leaves placeholders on disk: listing them is
metadata only, but *opening* one makes Windows download it. A content search
(``rg``/``grep``) opens every file, so a search over an OneDrive-synced home
pulled gigabytes and took a Windows machine down (SUP-20261005-102357).

Rules for content searches on a local Windows backend:

* a search that starts *above* a sync root skips the root entirely;
* a search inside a sync root walks it once (metadata only, bounded) and skips
  every online-only file through an rg ignore file;
* a tree too large to walk safely is refused with a hint to narrow the path.

File-name searches never open files and are not affected.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional

# FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
CLOUD_ONLY_ATTRIBUTES = 0x1000 | 0x40000 | 0x400000
SYNC_ROOT_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
WALK_LIMIT = 20_000


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def sync_roots(environ: Mapping[str, str] = os.environ, home: Optional[str] = None) -> List[str]:
    """OneDrive sync roots: the env vars OneDrive sets plus ``~/OneDrive*`` folders."""
    roots: List[str] = []
    for var in SYNC_ROOT_ENV_VARS:
        value = (environ.get(var) or "").strip()
        if value and os.path.isdir(value):
            roots.append(value)
    home = home or os.path.expanduser("~")
    try:
        for entry in os.scandir(home):
            if entry.name.lower().startswith("onedrive") and entry.is_dir(follow_symlinks=False):
                roots.append(entry.path)
    except OSError:
        pass
    unique, seen = [], set()
    for root in roots:
        key = _norm(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def is_cloud_only(entry: os.DirEntry) -> bool:
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & CLOUD_ONLY_ATTRIBUTES)


@dataclass
class CloudSearchPlan:
    """What a content search must skip to stay off the network."""

    skipped_roots: List[str] = field(default_factory=list)  # relative to the search path
    cloud_only_files: List[str] = field(default_factory=list)  # relative, POSIX separators
    refuse: Optional[str] = None

    @property
    def active(self) -> bool:
        return bool(self.skipped_roots or self.cloud_only_files or self.refuse)

    def note(self) -> str:
        parts = []
        if self.skipped_roots:
            parts.append(
                "skipped cloud-synced folder(s) "
                + ", ".join(self.skipped_roots)
                + " (content search would download online-only files; search inside a specific subfolder instead)"
            )
        if self.cloud_only_files:
            parts.append(f"skipped {len(self.cloud_only_files)} online-only file(s) that are not downloaded")
        return "; ".join(parts)


def plan_content_search(
    search_path: str,
    roots: Iterable[str],
    *,
    cloud_only: Optional[Callable[[os.DirEntry], bool]] = None,
    walk_limit: int = WALK_LIMIT,
) -> CloudSearchPlan:
    cloud_only = cloud_only or is_cloud_only
    plan = CloudSearchPlan()
    target = _norm(search_path)
    for root in roots:
        key = _norm(root)
        if target == key or target.startswith(key + os.sep):
            return _plan_inside_root(search_path, cloud_only, walk_limit)
        if key.startswith(target + os.sep):
            plan.skipped_roots.append(os.path.relpath(root, search_path).replace("\\", "/"))
    return plan


def _plan_inside_root(search_path: str, cloud_only, walk_limit: int) -> CloudSearchPlan:
    plan = CloudSearchPlan()
    if os.path.isfile(search_path):
        return plan  # one explicitly named file: the user asked for exactly this
    seen = 0
    stack = [search_path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > walk_limit:
                        plan.refuse = (
                            f"{search_path} is in cloud-synced storage (OneDrive) and holds more than "
                            f"{walk_limit} entries; a content search there would download online-only files. "
                            "Search a specific subfolder, search file names (target='files'), or use the "
                            "document/ticket tools for this question."
                        )
                        plan.cloud_only_files = []
                        return plan
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            stack.append(entry.path)
                    elif cloud_only(entry):
                        plan.cloud_only_files.append(
                            os.path.relpath(entry.path, search_path).replace("\\", "/")
                        )
        except OSError:
            continue
    return plan


def _gitignore_escape(rel: str) -> str:
    escaped = "".join("\\" + ch if ch in "\\[]*?!#" else ch for ch in rel)
    if escaped.startswith(" "):
        escaped = "\\" + escaped
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return escaped


def write_ignore_file(cloud_only_files: List[str]) -> str:
    """rg ignore file that skips the given files wherever the search runs from.

    ``**/`` keeps the rules independent of rg's working directory (ignore-file
    rules are matched relative to it).
    """
    fd, path = tempfile.mkstemp(prefix="hermes-cloud-only-", suffix=".ignore")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for rel in cloud_only_files:
            fh.write("**/" + _gitignore_escape(rel) + "\n")
    return path.replace("\\", "/")
