"""Release channels and release tags (AIS-292).

Releases are git tags on ``main``:

* ``vX.Y.Z-rc.N`` — a release *candidate* cut from ``main`` (``./createTag.sh
  patch|minor|major``); the workflow builds the installers and publishes a
  GitHub pre-release.
* ``vX.Y.Z`` — the *stable* release, created by ``./createTag.sh promote
  stable`` on exactly the commit of the highest candidate of that version;
  the workflow re-publishes the candidate's artifacts as the latest release.

Update channels map onto those tags:

* ``stable`` (alias ``tags``) — only ``vX.Y.Z`` tags.
* ``preview`` — the highest tag including candidates.
* ``main`` (or any other branch name) — follow the branch.

Everything in here is pure: no git, no network — the callers hand in the tag
names they fetched.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Iterable, List, Optional, Tuple

STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
RC_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$")

CHANNEL_STABLE = "stable"
CHANNEL_PREVIEW = "preview"
CHANNEL_MAIN = "main"
TAG_CHANNELS = (CHANNEL_STABLE, CHANNEL_PREVIEW)
_CHANNEL_ALIASES = {"tags": CHANNEL_STABLE, "release": CHANNEL_STABLE, "rc": CHANNEL_PREVIEW, "beta": CHANNEL_PREVIEW}

GITHUB_REPO = "IAMDS-GMBH/AIMDS-Agent"


def normalize_channel(name: Optional[str]) -> str:
    """``tags`` → ``stable``; empty → ``main``; branch names pass through."""
    value = str(name or "").strip()
    if not value:
        return CHANNEL_MAIN
    lowered = value.lower()
    return _CHANNEL_ALIASES.get(lowered, lowered if lowered in (CHANNEL_STABLE, CHANNEL_PREVIEW, CHANNEL_MAIN) else value)


def is_tag_channel(name: Optional[str]) -> bool:
    return normalize_channel(name) in TAG_CHANNELS


def parse_release_tag(tag: str) -> Optional[Tuple[int, int, int, Optional[int]]]:
    """``v1.2.3`` → ``(1, 2, 3, None)``; ``v1.2.3-rc.4`` → ``(1, 2, 3, 4)``; else ``None``."""
    value = str(tag or "").strip()
    m = STABLE_TAG_RE.match(value)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), None
    m = RC_TAG_RE.match(value)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def release_sort_key(tag: str) -> Tuple[int, int, int, int, int]:
    """Sort key: version, then stable above every candidate of the same version."""
    parsed = parse_release_tag(tag)
    if parsed is None:
        return (-1, -1, -1, -1, -1)
    major, minor, patch, rc = parsed
    return (major, minor, patch, 1 if rc is None else 0, rc or 0)


def is_stable_tag(tag: str) -> bool:
    return bool(STABLE_TAG_RE.match(str(tag or "").strip()))


def is_candidate_tag(tag: str) -> bool:
    return bool(RC_TAG_RE.match(str(tag or "").strip()))


def select_release_tag(tags: Iterable[str], channel: str) -> Optional[str]:
    """The tag an update should target for ``channel`` (``None`` when nothing fits).

    ``stable``: highest ``vX.Y.Z``. ``preview``: highest tag overall; a stable
    tag outranks the candidates of its own version. Non-release tags are ignored.
    """
    normalized = normalize_channel(channel)
    if normalized not in TAG_CHANNELS:
        return None
    candidates: List[str] = []
    for raw in tags or []:
        tag = str(raw or "").strip()
        if not parse_release_tag(tag):
            continue
        if normalized == CHANNEL_STABLE and not is_stable_tag(tag):
            continue
        candidates.append(tag)
    if not candidates:
        return None
    return max(candidates, key=release_sort_key)


def version_from_tag(tag: str) -> str:
    """``v1.2.3-rc.4`` → ``1.2.3-rc.4``."""
    value = str(tag or "").strip()
    return value[1:] if value.startswith("v") else value


def stable_version_of(tag: str) -> Optional[str]:
    parsed = parse_release_tag(tag)
    if parsed is None:
        return None
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"


def github_archive_url(ref: str, *, kind: str = "heads", repo: str = GITHUB_REPO) -> str:
    """Zip archive of a branch (``kind='heads'``) or tag (``kind='tags'``)."""
    kind_norm = "tags" if kind == "tags" else "heads"
    return f"https://github.com/{repo}/archive/refs/{kind_norm}/{ref}.zip"


def latest_release_tag_via_api(channel: str, *, timeout: float = 10.0, repo: str = GITHUB_REPO) -> Optional[str]:
    """Resolve the channel's tag from the GitHub Releases API (no git needed).

    Used by the Windows ZIP fallback, which runs exactly when local git file
    I/O is broken. ``stable`` → ``/releases/latest``; ``preview`` → highest
    release tag among the newest releases including pre-releases.
    """
    normalized = normalize_channel(channel)
    if normalized not in TAG_CHANNELS:
        return None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "hermes-agent/update"}
    try:
        if normalized == CHANNEL_STABLE:
            request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
            tag = str((data or {}).get("tag_name") or "")
            return tag if is_stable_tag(tag) else None
        request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=30", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "[]")
        tags = [str(r.get("tag_name") or "") for r in (data or []) if isinstance(r, dict) and not r.get("draft")]
        return select_release_tag(tags, CHANNEL_PREVIEW)
    except Exception:
        return None


__all__ = [
    "CHANNEL_MAIN",
    "CHANNEL_PREVIEW",
    "CHANNEL_STABLE",
    "GITHUB_REPO",
    "RC_TAG_RE",
    "STABLE_TAG_RE",
    "TAG_CHANNELS",
    "github_archive_url",
    "is_candidate_tag",
    "is_stable_tag",
    "is_tag_channel",
    "latest_release_tag_via_api",
    "normalize_channel",
    "parse_release_tag",
    "release_sort_key",
    "select_release_tag",
    "stable_version_of",
    "version_from_tag",
]
