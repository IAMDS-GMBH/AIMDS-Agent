"""Release marker — version identity of a source-archive installation (AIS-312).

An install that was applied from ``hermes-source-<ver>.zip`` (public release
repository, see ``hermes_cli/release_update.py``) has no git history to ask
"which release is this?". ``hermes update`` therefore writes
``<PROJECT_ROOT>/.hermes-release.json`` after every successful archive
update, and everything that needs the installed version reads it *first*:

* ``hermes_cli.__version__`` (banner, ``hermes --version``, telemetry)
* ``hermes_cli.config.detect_install_method`` → ``"release"``
* ``hermes update`` / ``--check`` / the dashboard and desktop update checks

Reading the marker first matters on a git checkout that was converted to
archive updates: ``git describe`` keeps naming the old tag because the tree
was replaced without moving HEAD, which is exactly the phantom-update loop
of AIS-297. The marker is the truth once it exists.

stdlib only — imported at package-init time by ``hermes_cli/__init__.py``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MARKER_FILENAME = ".hermes-release.json"
MARKER_FORMAT = "hermes-release-marker-v1"

_REQUIRED = ("channel", "tag", "version", "commit_sha")


def marker_path(project_root: Path) -> Path:
    return Path(project_root) / MARKER_FILENAME


def read_release_marker(project_root: Path) -> Optional[dict]:
    """The parsed marker, or ``None`` when absent, unreadable or malformed.

    A malformed marker is treated exactly like a missing one so a corrupt
    file can never wedge the updater; the next archive update rewrites it.
    """
    path = marker_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("format") != MARKER_FORMAT:
        return None
    for key in _REQUIRED:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
    return data


def write_release_marker(
    project_root: Path,
    *,
    channel: str,
    tag: str,
    version: str,
    commit_sha: str,
    sha256: str,
    build_id: str = "",
    applied_at: Optional[str] = None,
) -> Path:
    """Atomically write the marker (temp file + ``os.replace``) and return its path."""
    path = marker_path(project_root)
    payload = {
        "format": MARKER_FORMAT,
        "channel": channel,
        "tag": tag,
        "version": version,
        "commit_sha": commit_sha,
        "sha256": sha256,
        "build_id": build_id or "",
        "applied_at": applied_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def remove_release_marker(project_root: Path) -> bool:
    """Delete the marker (e.g. after a git/legacy update re-established git identity)."""
    path = marker_path(project_root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def is_release_managed(project_root: Path) -> bool:
    return read_release_marker(project_root) is not None


__all__ = [
    "MARKER_FILENAME",
    "MARKER_FORMAT",
    "is_release_managed",
    "marker_path",
    "read_release_marker",
    "remove_release_marker",
    "write_release_marker",
]
