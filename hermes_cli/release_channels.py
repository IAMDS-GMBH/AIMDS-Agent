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
* ``auto`` (config default ``updates.channel``, AIS-299) — ``stable`` on a
  detached checkout (installed clients sit on a release tag), the branch
  default on a named branch (developer checkouts stay on ``main``).

Everything in here is pure: no git, no network — the callers hand in the tag
names they fetched. The two GitHub API helpers at the bottom are the only
exception; they are shared by the release-asset updater (AIS-312) and the
legacy source-archive fallback.
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
CHANNEL_AUTO = "auto"
TAG_CHANNELS = (CHANNEL_STABLE, CHANNEL_PREVIEW)
_CHANNEL_ALIASES = {"tags": CHANNEL_STABLE, "release": CHANNEL_STABLE, "rc": CHANNEL_PREVIEW, "beta": CHANNEL_PREVIEW}

GITHUB_REPO = "IAMDS-GMBH/AIMDS-Agent"

# Public mirror of every release (AIS-311): README + releases only. Clients
# without access to GITHUB_REPO update from here (AIS-312).
RELEASE_REPO = "IAMDS-GMBH/AIMDS-Agent-Releases"
RELEASE_MANIFEST_ASSET = "hermes-release.json"

# ``updates.source`` — where ``hermes update`` gets the code from (AIS-312).
# NOT a channel: ``release`` here must never go through ``normalize_channel``
# (which aliases the *channel* name ``release`` to ``stable``).
SOURCE_AUTO = "auto"
SOURCE_GIT = "git"
SOURCE_RELEASE = "release"
UPDATE_SOURCES = (SOURCE_AUTO, SOURCE_GIT, SOURCE_RELEASE)


def normalize_channel(name: Optional[str]) -> str:
    """``tags`` → ``stable``; empty → ``main``; branch names pass through."""
    value = str(name or "").strip()
    if not value:
        return CHANNEL_MAIN
    lowered = value.lower()
    if lowered in (CHANNEL_STABLE, CHANNEL_PREVIEW, CHANNEL_MAIN, CHANNEL_AUTO):
        return lowered
    return _CHANNEL_ALIASES.get(lowered, value)


def is_tag_channel(name: Optional[str]) -> bool:
    return normalize_channel(name) in TAG_CHANNELS


def normalize_update_source(name: Optional[str]) -> str:
    """``updates.source`` value → ``auto`` | ``git`` | ``release`` (unknown → ``auto``)."""
    value = str(name or "").strip().lower()
    return value if value in UPDATE_SOURCES else SOURCE_AUTO


def tag_fits_channel(tag: str, channel: str) -> bool:
    """Whether a release tag may be offered on ``channel``.

    ``stable`` accepts only ``vX.Y.Z``; ``preview`` accepts stable and
    candidate tags. Branch channels (``main``) never fit a tag.
    """
    normalized = normalize_channel(channel)
    if normalized == CHANNEL_STABLE:
        return is_stable_tag(tag)
    if normalized == CHANNEL_PREVIEW:
        return parse_release_tag(tag) is not None
    return False


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


def compare_release_tags(a: str, b: str) -> int:
    """``-1``/``0``/``1`` by :func:`release_sort_key` (stable above its own candidates)."""
    ka, kb = release_sort_key(a), release_sort_key(b)
    return (ka > kb) - (ka < kb)


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


def head_release_tag(tags_on_head: Iterable[str]) -> Optional[str]:
    """The highest *release* tag among the tags pointing at HEAD (``git tag --points-at HEAD``).

    Non-release tags (``nightly-1``) are ignored; ``None`` when HEAD carries none.
    After ``createTag.sh promote stable`` a commit carries both ``vX.Y.Z-rc.N``
    and ``vX.Y.Z`` — the stable tag wins.
    """
    best: Optional[str] = None
    for raw in tags_on_head or []:
        tag = str(raw or "").strip()
        if not parse_release_tag(tag):
            continue
        if best is None or compare_release_tags(tag, best) > 0:
            best = tag
    return best


def release_tag_is_newer(candidate: Optional[str], target: str) -> bool:
    """True iff ``candidate`` is a release tag sorting strictly above ``target``."""
    if not candidate or not parse_release_tag(candidate) or not parse_release_tag(target):
        return False
    return compare_release_tags(candidate, target) > 0


HEAD_AT_TARGET = "at-target"
HEAD_NEWER_RELEASE = "newer"
HEAD_OTHER = "other"


def resolve_head_vs_target(
    *,
    head_sha: str,
    target_sha: str,
    head_tags: Iterable[str],
    target_tag: str,
) -> str:
    """Classify HEAD against the channel's target tag (AIS-299).

    * ``at-target`` — HEAD is the target commit (sha equality wins, so a
      candidate that was just promoted to stable on the same commit is up to
      date on both channels).
    * ``newer`` — HEAD sits on a release tag that sorts *above* the target
      (``v0.7.5-rc.1`` on the ``stable`` channel while ``v0.7.4`` is the
      latest stable). Nothing to install; never a downgrade.
    * ``other`` — anything else: the caller decides between "behind" (a real
      update) and "untagged checkout past the release" via ``rev-list``.
    """
    head = str(head_sha or "").strip()
    target = str(target_sha or "").strip()
    if head and target and head == target:
        return HEAD_AT_TARGET
    if release_tag_is_newer(head_release_tag(head_tags), target_tag):
        return HEAD_NEWER_RELEASE
    return HEAD_OTHER


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


def release_download_url(tag: str, asset: str, *, repo: str = RELEASE_REPO) -> str:
    """``https://github.com/<repo>/releases/download/<tag>/<asset>``."""
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def latest_manifest_url(*, repo: str = RELEASE_REPO) -> str:
    """Static URL of the latest *stable* release manifest.

    GitHub redirects ``releases/latest/download/<asset>`` to the newest
    non-draft, non-prerelease release — exactly the ``stable`` channel — and
    it is not subject to the API rate limit.
    """
    return f"https://github.com/{repo}/releases/latest/download/{RELEASE_MANIFEST_ASSET}"


def _github_api_headers() -> dict:
    import os

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "hermes-agent/update"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_release_via_api(channel: str, *, timeout: float = 10.0, repo: str = RELEASE_REPO) -> Optional[dict]:
    """The GitHub release object (``tag_name``, ``assets`` …) the channel targets, or ``None``.

    ``stable`` → ``/releases/latest`` (only if its tag is ``vX.Y.Z``);
    ``preview`` → the highest release tag among the newest 30 non-draft
    releases including pre-releases. Every failure collapses to ``None``.
    """
    normalized = normalize_channel(channel)
    if normalized not in TAG_CHANNELS:
        return None
    headers = _github_api_headers()
    try:
        if normalized == CHANNEL_STABLE:
            request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
            if not isinstance(data, dict):
                return None
            tag = str(data.get("tag_name") or "")
            return data if is_stable_tag(tag) else None
        request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=30", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "[]")
        releases = [r for r in (data or []) if isinstance(r, dict) and not r.get("draft")]
        best = select_release_tag([str(r.get("tag_name") or "") for r in releases], CHANNEL_PREVIEW)
        if not best:
            return None
        return next((r for r in releases if str(r.get("tag_name") or "") == best), None)
    except Exception:
        return None


def latest_release_tag_via_api(channel: str, *, timeout: float = 10.0, repo: str = GITHUB_REPO) -> Optional[str]:
    """Resolve the channel's tag from the GitHub Releases API (no git needed).

    Used by the legacy source-archive fallback (``_update_via_legacy_archive``),
    which runs when neither git nor the public release repository can serve
    the update; it therefore defaults to the *source* repository.
    """
    release = fetch_release_via_api(channel, timeout=timeout, repo=repo)
    if not release:
        return None
    tag = str(release.get("tag_name") or "")
    return tag or None


__all__ = [
    "CHANNEL_AUTO",
    "CHANNEL_MAIN",
    "CHANNEL_PREVIEW",
    "CHANNEL_STABLE",
    "GITHUB_REPO",
    "RELEASE_MANIFEST_ASSET",
    "RELEASE_REPO",
    "SOURCE_AUTO",
    "SOURCE_GIT",
    "SOURCE_RELEASE",
    "UPDATE_SOURCES",
    "HEAD_AT_TARGET",
    "HEAD_NEWER_RELEASE",
    "HEAD_OTHER",
    "RC_TAG_RE",
    "STABLE_TAG_RE",
    "TAG_CHANNELS",
    "compare_release_tags",
    "fetch_release_via_api",
    "github_archive_url",
    "head_release_tag",
    "is_candidate_tag",
    "is_stable_tag",
    "is_tag_channel",
    "latest_manifest_url",
    "latest_release_tag_via_api",
    "normalize_channel",
    "normalize_update_source",
    "parse_release_tag",
    "release_download_url",
    "release_sort_key",
    "release_tag_is_newer",
    "resolve_head_vs_target",
    "select_release_tag",
    "stable_version_of",
    "tag_fits_channel",
    "version_from_tag",
]
