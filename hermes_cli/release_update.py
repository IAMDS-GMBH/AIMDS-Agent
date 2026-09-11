"""Update from the public release repository's source archives (AIS-312).

Every release of the (private) source repository is mirrored into the public
``IAMDS-GMBH/AIMDS-Agent-Releases`` (AIS-311) with, per tag::

    hermes-source-<ver>.zip          git archive, root folder hermes-agent-<ver>/
    hermes-source-<ver>.zip.sha256
    hermes-release.json              {format: hermes-release-v1, version, tag,
                                      commit_sha, source_archive, sha256, size,
                                      built_at}   (scripts/build_source_package.sh)

``hermes update`` uses this path for installations that cannot reach the
source repository via git (no ``.git``, origin unreachable, or
``updates.source: release``). The manifest of the ``stable`` channel is read
from the static ``releases/latest/download/`` URL (no API, no rate limit);
``preview`` resolves the newest release including candidates via the API.

Everything here is fail-closed and side-effect free until a manifest has
fully validated and the archive hash matched: any missing, unreachable,
malformed, wrong-channel or hash-invalid feed raises :class:`ReleaseFeedError`
and leaves the installation untouched. Callers decide whether to fall back
to git (``updates.source: auto``) or to fail loudly (``release``).

Validation rules — keep in sync with ``scripts/build_source_package.sh`` and
``apps/desktop/electron/update-channels.cjs`` (``validateReleaseManifest``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from hermes_cli.release_channels import (
    CHANNEL_STABLE,
    RELEASE_MANIFEST_ASSET,
    RELEASE_REPO,
    compare_release_tags,
    fetch_release_via_api,
    is_tag_channel,
    latest_manifest_url,
    normalize_channel,
    parse_release_tag,
    release_download_url,
    tag_fits_channel,
    version_from_tag,
)

logger = logging.getLogger(__name__)

MANIFEST_FORMAT = "hermes-release-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# A manifest is a handful of short scalars; anything larger is not one.
_MANIFEST_MAX_BYTES = 64 * 1024
# The source package is ~40 MB. The cap only exists so a hostile or broken
# mirror cannot fill the disk before the hash check runs.
_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
_DOWNLOAD_CHUNK = 1024 * 1024

MANIFEST_TIMEOUT = 10
ARCHIVE_TIMEOUT = 300

# Top-level names that belong to the user or the install, not to the source
# archive. Superset of the git update flow's exclusions.
PRESERVE_ENTRIES = frozenset(
    {
        "venv",
        ".venv",
        "node_modules",
        ".git",
        ".env",
        ".worktrees",
        ".hermes-release.json",
        ".update-incomplete",
        ".update-incomplete.lock",
    }
)

# A valid extracted archive must carry these. Subset of
# ``main._UPDATE_CRITICAL_FILES`` — enough to prove we are about to overwrite
# the install with a hermes-agent tree and not with something else.
REQUIRED_MARKERS = (
    "pyproject.toml",
    "run_agent.py",
    os.path.join("hermes_cli", "main.py"),
    os.path.join("hermes_cli", "config.py"),
)

FEED_SAME = "same"
FEED_NEWER = "newer"
FEED_OLDER = "older"


class ReleaseFeedError(Exception):
    """The release manifest or archive was absent, untrusted, or unusable.

    The message is a concise, user-facing reason.
    """


@dataclass(frozen=True)
class ReleaseFeed:
    """A validated release manifest, safe to act on."""

    version: str
    tag: str
    commit_sha: str
    package_url: str
    sha256: str
    size: int
    build_id: str
    channel: str


def is_source_tree(project_root: Path) -> bool:
    """Whether ``project_root`` looks like a hermes-agent source tree."""
    root = Path(project_root)
    return all((root / marker).exists() for marker in REQUIRED_MARKERS)


# =========================================================================
# Manifest fetch + validation
# =========================================================================

def _fetch_bytes(url: str, *, timeout: float, max_bytes: int, accept: str = "application/json") -> bytes:
    """GET ``url`` with a size cap. Raises ``ReleaseFeedError``."""
    try:
        request = urllib.request.Request(
            url, headers={"Accept": accept, "User-Agent": "hermes-agent/update"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except Exception as exc:
        raise ReleaseFeedError(f"{url} unreachable ({exc})") from exc
    if len(raw) > max_bytes:
        raise ReleaseFeedError(f"{url} is implausibly large")
    return raw


def _parse_manifest_bytes(raw: bytes) -> dict:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ReleaseFeedError(f"release manifest is not valid JSON ({exc})") from exc
    if not isinstance(manifest, dict):
        raise ReleaseFeedError("release manifest is not a JSON object")
    return manifest


def _require_str(manifest: dict, key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise ReleaseFeedError(f"manifest field '{key}' is missing or not a string")
    return value


def validate_manifest(
    manifest: dict,
    *,
    channel: str,
    repo: str = RELEASE_REPO,
    release_tag: Optional[str] = None,
) -> ReleaseFeed:
    """Validate a ``hermes-release.json`` for ``channel``.

    Raises ``ReleaseFeedError`` with a concise reason on the first failure.
    Unknown manifest fields are ignored. ``release_tag`` (the GitHub release
    the manifest was downloaded from) must match the manifest's ``tag`` when
    given.
    """
    if not isinstance(manifest, dict):
        raise ReleaseFeedError("manifest is not a JSON object")

    fmt = manifest.get("format")
    if fmt != MANIFEST_FORMAT:
        raise ReleaseFeedError(f"unsupported manifest format {fmt!r}")

    normalized = normalize_channel(channel)
    version = _require_str(manifest, "version")
    tag = _require_str(manifest, "tag")
    commit_sha = _require_str(manifest, "commit_sha")
    source_archive = _require_str(manifest, "source_archive")
    sha256 = _require_str(manifest, "sha256")
    built_at = _require_str(manifest, "built_at")

    if parse_release_tag(tag) is None:
        raise ReleaseFeedError(f"tag {tag!r} is not a release tag (vX.Y.Z or vX.Y.Z-rc.N)")
    if release_tag is not None and tag != release_tag:
        raise ReleaseFeedError(f"manifest tag {tag!r} does not match release {release_tag!r}")
    if not tag_fits_channel(tag, normalized):
        raise ReleaseFeedError(f"tag {tag!r} is not a {normalized} release")
    if version != version_from_tag(tag):
        raise ReleaseFeedError(f"version {version!r} does not match tag {tag!r}")
    if not _COMMIT_RE.match(commit_sha):
        raise ReleaseFeedError("commit_sha is not 40 lowercase hex characters")
    if not _SHA256_RE.match(sha256):
        raise ReleaseFeedError("sha256 is not 64 lowercase hex characters")

    size = manifest.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ReleaseFeedError("manifest field 'size' is not a positive integer")

    expected_archive = f"hermes-source-{version}.zip"
    if source_archive != expected_archive:
        raise ReleaseFeedError(
            f"source_archive {source_archive!r} is not the expected {expected_archive!r}"
        )

    try:
        datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseFeedError(f"built_at {built_at!r} is not an ISO 8601 timestamp") from exc

    package_url = release_download_url(tag, source_archive, repo=repo)
    parts = urlsplit(package_url)
    if parts.scheme != "https" or (parts.hostname or "").lower() != "github.com":
        raise ReleaseFeedError("package URL is not an HTTPS github.com URL")
    if not parts.path.startswith(f"/{repo}/releases/download/"):
        raise ReleaseFeedError(f"package URL is not under the release repository {repo}")

    return ReleaseFeed(
        version=version,
        tag=tag,
        commit_sha=commit_sha,
        package_url=package_url,
        sha256=sha256,
        size=size,
        build_id=str(manifest.get("build_id") or built_at),
        channel=normalized,
    )


def fetch_release_feed(
    channel: str, *, timeout: float = MANIFEST_TIMEOUT, repo: str = RELEASE_REPO
) -> ReleaseFeed:
    """Resolve and validate the manifest the channel targets.

    Read-only: one or two bounded HTTPS GETs, no filesystem writes. Never
    returns ``None`` — every failure is a ``ReleaseFeedError`` so callers can
    print the reason (AIS-297: no silent fallbacks).
    """
    normalized = normalize_channel(channel)
    if not is_tag_channel(normalized):
        raise ReleaseFeedError(f"channel {normalized!r} is not a release channel")

    if normalized == CHANNEL_STABLE:
        url = latest_manifest_url(repo=repo)
        manifest = _parse_manifest_bytes(_fetch_bytes(url, timeout=timeout, max_bytes=_MANIFEST_MAX_BYTES))
        return validate_manifest(manifest, channel=normalized, repo=repo)

    release = fetch_release_via_api(normalized, timeout=timeout, repo=repo)
    if not release:
        raise ReleaseFeedError(f"no {normalized} release found in {repo}")
    tag = str(release.get("tag_name") or "")
    assets = {
        str(a.get("name") or ""): a
        for a in (release.get("assets") or [])
        if isinstance(a, dict)
    }
    asset = assets.get(RELEASE_MANIFEST_ASSET)
    url = str((asset or {}).get("browser_download_url") or "")
    if not url:
        raise ReleaseFeedError(f"release {tag} has no {RELEASE_MANIFEST_ASSET} asset")
    manifest = _parse_manifest_bytes(_fetch_bytes(url, timeout=timeout, max_bytes=_MANIFEST_MAX_BYTES))
    return validate_manifest(manifest, channel=normalized, repo=repo, release_tag=tag)


def classify_feed(feed: ReleaseFeed, *, marker: Optional[dict], current_version: str) -> str:
    """``same`` / ``newer`` / ``older`` — the feed relative to what is installed.

    With a marker the commit is authoritative (same commit ⇒ ``same`` even
    when a candidate was promoted to stable under a new tag). Without one the
    installed version string is compared as a tag; an unparsable version
    (dev checkout, ``0.0.0``) counts as older than any release.
    """
    if marker:
        if str(marker.get("commit_sha") or "") == feed.commit_sha:
            return FEED_SAME
        cmp = compare_release_tags(feed.tag, str(marker.get("tag") or ""))
        if cmp < 0:
            return FEED_OLDER
        # Equal tag but different commit: the manifest is authoritative —
        # re-applying rewrites the marker instead of looping forever.
        return FEED_NEWER
    current_tag = f"v{str(current_version or '').strip()}"
    if parse_release_tag(current_tag) is None:
        return FEED_NEWER
    cmp = compare_release_tags(feed.tag, current_tag)
    if cmp == 0:
        return FEED_SAME
    return FEED_NEWER if cmp > 0 else FEED_OLDER


# =========================================================================
# Archive download, verification and application
# =========================================================================

def _download_verified_archive(feed: ReleaseFeed, dest: Path, timeout: int) -> None:
    """Stream the archive to ``dest``, hashing as we go. Raises on mismatch."""
    digest = hashlib.sha256()
    total = 0
    try:
        request = urllib.request.Request(
            feed.package_url, headers={"User-Agent": "hermes-agent/update"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _ARCHIVE_MAX_BYTES:
                        raise ReleaseFeedError("archive exceeds the maximum allowed size")
                    digest.update(chunk)
                    fh.write(chunk)
    except ReleaseFeedError:
        raise
    except Exception as exc:
        raise ReleaseFeedError(f"archive download failed ({exc})") from exc

    if feed.size and total != feed.size:
        raise ReleaseFeedError(f"archive size mismatch (expected {feed.size} bytes, got {total})")
    actual = digest.hexdigest()
    if actual != feed.sha256:
        raise ReleaseFeedError(
            f"archive sha256 mismatch (expected {feed.sha256}, got {actual})"
        )


def _safe_extract(zip_path: Path, dest_dir: Path) -> None:
    """Extract after rejecting traversal, absolute and symlink members.

    A ``git archive`` of hermes-agent never contains symlinks or paths outside
    its own root, so anything that does is a compromised or hostile mirror.
    """
    dest_real = os.path.realpath(str(dest_dir))
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for member in zf.infolist():
            name = member.filename
            if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
                raise ReleaseFeedError(f"archive contains an absolute path: {name}")
            if any(part == ".." for part in name.replace("\\", "/").split("/")):
                raise ReleaseFeedError(f"archive contains a parent reference: {name}")
            member_path = os.path.realpath(os.path.join(str(dest_dir), name))
            if (
                not member_path.startswith(dest_real + os.sep)
                and member_path != dest_real
            ):
                raise ReleaseFeedError(
                    f"zip-slip detected: {name} escapes the extraction directory"
                )
            # Unix mode lives in the upper 16 bits of external_attr.
            mode = (member.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ReleaseFeedError(f"archive contains a symlink member: {name}")
        zf.extractall(str(dest_dir))


def _resolve_archive_root(extract_dir: Path) -> Path:
    """Locate and validate the single source-archive root directory.

    The root's *name* is never checked (``hermes-agent-<ver>``, ``<repo>-<ref>``
    … differ between the release package and the legacy GitHub archive);
    the required project files inside it are.
    """
    entries = [
        p for p in extract_dir.iterdir() if p.name != "__MACOSX" and not p.name.startswith(".")
    ]
    dirs = [p for p in entries if p.is_dir()]
    if len(entries) != 1 or len(dirs) != 1:
        raise ReleaseFeedError(
            "archive does not contain exactly one source-archive root directory"
        )
    root = dirs[0]
    missing = [m for m in REQUIRED_MARKERS if not (root / m).exists()]
    if missing:
        raise ReleaseFeedError(
            f"archive is missing expected project files: {', '.join(missing)}"
        )
    return root


def _replace_tree(
    source_root: Path,
    project_root: Path,
    rollback_dir: Path,
    *,
    verify: Optional[Callable[[Path], bool]] = None,
) -> int:
    """Swap the archive's top-level entries into the install, atomically-ish.

    Each replaced entry is moved aside into ``rollback_dir`` first, so a
    failure part-way through can put every original back. Entries in
    ``PRESERVE_ENTRIES`` are never touched, and destination-only entries are
    left alone (the archive is not authoritative about user-created files).
    ``verify`` runs on the replaced tree inside the guarded section; returning
    ``False`` rolls everything back.
    """
    rollback_dir.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    replaced = 0
    try:
        for src in sorted(source_root.iterdir()):
            if src.name in PRESERVE_ENTRIES:
                continue
            dst = project_root / src.name
            if dst.exists() or dst.is_symlink():
                stashed = rollback_dir / src.name
                shutil.move(str(dst), str(stashed))
                moved.append((dst, stashed))
            else:
                created.append(dst)
            if src.is_dir():
                shutil.copytree(str(src), str(dst), symlinks=False)
            else:
                shutil.copy2(str(src), str(dst))
            replaced += 1
        if verify is not None and not verify(project_root):
            raise ReleaseFeedError("replaced tree failed verification")
    except Exception as exc:
        for dst in reversed(created):
            try:
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst), ignore_errors=True)
                elif dst.exists() or dst.is_symlink():
                    dst.unlink()
            except Exception:
                logger.exception("Failed to remove %s during release update rollback", dst)
        for dst, stashed in reversed(moved):
            try:
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst), ignore_errors=True)
                elif dst.exists() or dst.is_symlink():
                    dst.unlink()
                shutil.move(str(stashed), str(dst))
            except Exception:
                logger.exception("Failed to roll back %s during release update", dst)
        raise ReleaseFeedError(f"tree replacement failed and was rolled back ({exc})") from exc
    return replaced


def apply_archive(
    archive: Path,
    project_root: Path,
    *,
    work_dir: Path,
    verify: Optional[Callable[[Path], bool]] = None,
) -> int:
    """Extract an already-verified zip under ``work_dir`` and swap it into ``project_root``."""
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, extract_dir)
    source_root = _resolve_archive_root(extract_dir)
    return _replace_tree(source_root, Path(project_root), work_dir / "rollback", verify=verify)


def apply_release_update(
    feed: ReleaseFeed,
    project_root: Path,
    *,
    timeout: int = ARCHIVE_TIMEOUT,
    verify: Optional[Callable[[Path], bool]] = None,
) -> int:
    """Download, verify and apply ``feed`` to ``project_root``.

    Returns the number of replaced top-level entries. Raises
    ``ReleaseFeedError`` on any failure, in which case ``project_root`` is left
    exactly as it was found.
    """
    project_root = Path(project_root)
    # Staged outside the install tree so a failed run never leaves partial
    # archive content behind inside it.
    tmp_dir = Path(tempfile.mkdtemp(prefix="hermes-release-update-"))
    try:
        archive = tmp_dir / "hermes-source.zip"
        _download_verified_archive(feed, archive, timeout)
        return apply_archive(archive, project_root, work_dir=tmp_dir, verify=verify)
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


__all__ = [
    "ARCHIVE_TIMEOUT",
    "FEED_NEWER",
    "FEED_OLDER",
    "FEED_SAME",
    "MANIFEST_FORMAT",
    "MANIFEST_TIMEOUT",
    "PRESERVE_ENTRIES",
    "REQUIRED_MARKERS",
    "ReleaseFeed",
    "ReleaseFeedError",
    "apply_archive",
    "apply_release_update",
    "classify_feed",
    "fetch_release_feed",
    "is_source_tree",
    "validate_manifest",
]
