"""Suite-published update feed for source installations.

A configured AIMDS Suite host may publish a stable update feed alongside the
GitHub source of truth::

    https://<suite-host>/client-update/HermesSuite.package   (JSON manifest)
    https://<suite-host>/client-update/HermesSuite.zip       (GitHub source archive)

``hermes update`` consults this feed before the normal git path so a fleet
pinned to a Suite deployment converges on the version that deployment blessed,
without every client having to reach github.com.

Everything here is fail-closed and side-effect free until a manifest has fully
validated: any missing, unreachable, malformed, untrusted, non-newer,
wrong-channel or hash-invalid feed leaves the installation untouched and the
caller falls back to the existing git update path.
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
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from hermes_cli.version_utils import version_tuple

logger = logging.getLogger(__name__)

MANIFEST_FORMAT = "hermes-source-archive-v1"
VALID_CHANNELS = ("prod", "staging", "main-branch", "tag-based")

_SUITE_PROVIDER_KEY = "aimds-suite-prod"
_FEED_PATH = "/client-update/HermesSuite.package"

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A manifest is a handful of short scalars; anything larger is not one.
_MANIFEST_MAX_BYTES = 64 * 1024
# A hermes-agent source archive is a few MB. The cap only exists so a
# hostile or broken feed cannot fill the disk before the hash check runs.
_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
_DOWNLOAD_CHUNK = 1024 * 1024

MANIFEST_TIMEOUT = 10
ARCHIVE_TIMEOUT = 300

# Top-level names that belong to the user or the install, not to the source
# archive. Superset of the git update flow's exclusions and of the preserve
# set in ``main._update_via_zip``.
PRESERVE_ENTRIES = frozenset(
    {"venv", ".venv", "node_modules", ".git", ".env", ".worktrees"}
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


class SuiteFeedError(Exception):
    """A Suite update feed was absent, untrusted, or unusable.

    The message is a concise, user-facing reason; callers log it and fall
    back to the git update path.
    """


@dataclass(frozen=True)
class SuiteFeed:
    """A validated Suite manifest, safe to act on."""

    version: str
    target_ref: str
    package_url: str
    sha256: str
    repository: str

    @property
    def repository_name(self) -> str:
        return self.repository.split("/", 1)[1]


# =========================================================================
# Resolution of the trust anchors (Suite host + trusted repository)
# =========================================================================

def _normalize_host(url: str) -> Optional[str]:
    """Return ``host`` or ``host:port`` for an HTTPS URL, else ``None``.

    Only the hostname and an *explicit* port participate; path, query and
    userinfo are ignored so a provider ``base_url`` pointing at an API
    endpoint still matches a bare-host package URL.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return f"{host}:{port}" if port is not None else host


def resolve_suite_host(config: dict) -> Optional[str]:
    """Normalized host of the configured Suite provider, or ``None``."""
    try:
        base_url = (
            (config or {})
            .get("providers", {})
            .get(_SUITE_PROVIDER_KEY, {})
            .get("base_url")
        )
    except AttributeError:
        return None
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return _normalize_host(base_url.strip())


def _repo_from_origin(origin_url: str) -> Optional[str]:
    """Extract ``owner/repo`` from an https:// or scp-style git remote URL."""
    value = origin_url.strip().rstrip("/")
    if not value:
        return None
    if value.startswith("git@"):
        # scp-style: git@github.com:owner/repo.git
        path = value.partition(":")[2]
    else:
        path = urlsplit(value).path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return None
    candidate = f"{segments[-2]}/{segments[-1]}"
    return candidate if _REPO_RE.match(candidate) else None


def resolve_trusted_repository(
    config: dict, origin_url: Optional[str]
) -> Optional[str]:
    """The ``owner/repo`` a Suite manifest must declare, or ``None``.

    ``updates.source_repository`` wins; otherwise it is derived from the
    checkout's git origin. ``None`` means "no trust anchor" and callers must
    skip the Suite feed entirely rather than guess.
    """
    try:
        configured = (config or {}).get("updates", {}).get("source_repository")
    except AttributeError:
        configured = None
    if isinstance(configured, str) and configured.strip():
        candidate = configured.strip()
        if _REPO_RE.match(candidate):
            return candidate
        logger.debug("Ignoring malformed updates.source_repository: %r", candidate)
        return None
    if not origin_url:
        return None
    return _repo_from_origin(origin_url)


def resolve_channel(config: dict) -> Optional[str]:
    """The configured update channel, or ``None`` if it is not a valid one."""
    try:
        channel = (config or {}).get("updates", {}).get("channel")
    except AttributeError:
        return None
    if channel is None or (isinstance(channel, str) and not channel.strip()):
        return "prod"
    if isinstance(channel, str) and channel.strip() in VALID_CHANNELS:
        return channel.strip()
    logger.debug("Ignoring invalid updates.channel: %r", channel)
    return None


# =========================================================================
# Manifest fetch + validation
# =========================================================================

def fetch_manifest(suite_host: str, *, timeout: int = MANIFEST_TIMEOUT) -> dict:
    """GET and parse the Suite update manifest. Raises ``SuiteFeedError``."""
    url = f"https://{suite_host}{_FEED_PATH}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MANIFEST_MAX_BYTES + 1)
    except Exception as exc:
        raise SuiteFeedError(f"feed unreachable ({exc})") from exc
    if len(raw) > _MANIFEST_MAX_BYTES:
        raise SuiteFeedError("feed manifest is implausibly large")
    try:
        manifest = json.loads(raw)
    except Exception as exc:
        raise SuiteFeedError(f"feed manifest is not valid JSON ({exc})") from exc
    if not isinstance(manifest, dict):
        raise SuiteFeedError("feed manifest is not a JSON object")
    return manifest


def _require_str(manifest: dict, key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise SuiteFeedError(f"manifest field '{key}' is missing or not a string")
    return value


def validate_manifest(
    manifest: dict,
    *,
    trusted_repository: str,
    suite_host: str,
    channel: str,
    current_version: str,
) -> SuiteFeed:
    """Validate a manifest against the local trust anchors.

    Raises ``SuiteFeedError`` with a concise reason on the first failure.
    Unknown manifest fields are ignored.
    """
    if not isinstance(manifest, dict):
        raise SuiteFeedError("manifest is not a JSON object")

    fmt = manifest.get("format")
    if fmt != MANIFEST_FORMAT:
        raise SuiteFeedError(f"unsupported manifest format {fmt!r}")

    version = _require_str(manifest, "version")
    source_repository = _require_str(manifest, "source_repository")
    target_ref = _require_str(manifest, "target_ref")
    package_url = _require_str(manifest, "package_url")
    sha256 = _require_str(manifest, "sha256")
    manifest_channel = _require_str(manifest, "channel")
    # Accepted and type-checked; not otherwise used yet.
    _require_str(manifest, "build_id")
    if not isinstance(manifest.get("settings", {}), dict):
        raise SuiteFeedError("manifest field 'settings' is not an object")

    if source_repository != trusted_repository:
        raise SuiteFeedError(
            f"manifest repository {source_repository!r} is not the trusted "
            f"repository {trusted_repository!r}"
        )

    tag_match = _TAG_RE.match(target_ref)
    if not tag_match:
        raise SuiteFeedError(f"target_ref {target_ref!r} is not a vX.Y.Z tag")
    if version != tag_match.group(1):
        raise SuiteFeedError(
            f"version {version!r} does not match target_ref {target_ref!r}"
        )

    if manifest_channel != channel:
        raise SuiteFeedError(
            f"manifest channel {manifest_channel!r} does not match the "
            f"configured channel {channel!r}"
        )

    package_host = _normalize_host(package_url)
    if package_host is None:
        raise SuiteFeedError("package_url is not an HTTPS URL")
    if package_host != suite_host:
        raise SuiteFeedError(
            f"package_url host {package_host!r} is not the configured Suite "
            f"host {suite_host!r}"
        )

    if version_tuple(version) <= version_tuple(current_version):
        raise SuiteFeedError(
            f"feed version {version} is not newer than {current_version}"
        )

    if not _SHA256_RE.match(sha256):
        raise SuiteFeedError("sha256 is not 64 lowercase hex characters")

    return SuiteFeed(
        version=version,
        target_ref=target_ref,
        package_url=package_url,
        sha256=sha256,
        repository=source_repository,
    )


def check_suite_update(
    config: dict, origin_url: Optional[str], current_version: str
) -> Optional[SuiteFeed]:
    """Return a validated newer Suite feed, or ``None``.

    Read-only: performs one bounded HTTPS GET and never touches the
    filesystem. Every failure mode collapses to ``None`` so callers can treat
    "no Suite update" and "Suite feed unusable" identically.
    """
    try:
        suite_host = resolve_suite_host(config)
        if not suite_host:
            logger.debug("No usable HTTPS base_url for %s", _SUITE_PROVIDER_KEY)
            return None
        trusted_repository = resolve_trusted_repository(config, origin_url)
        if not trusted_repository:
            logger.debug("No trusted source repository resolved; skipping Suite feed")
            return None
        channel = resolve_channel(config)
        if not channel:
            return None
        manifest = fetch_manifest(suite_host)
        return validate_manifest(
            manifest,
            trusted_repository=trusted_repository,
            suite_host=suite_host,
            channel=channel,
            current_version=current_version,
        )
    except SuiteFeedError as exc:
        logger.debug("Suite update feed rejected: %s", exc)
        return None
    except Exception as exc:  # never let the feed break the caller
        logger.debug("Suite update check failed: %s", exc)
        return None


# =========================================================================
# Archive download, verification and application
# =========================================================================

def _download_verified_archive(feed: SuiteFeed, dest: Path, timeout: int) -> None:
    """Stream the archive to ``dest``, hashing as we go. Raises on mismatch."""
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(feed.package_url, timeout=timeout) as resp:
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _ARCHIVE_MAX_BYTES:
                        raise SuiteFeedError("archive exceeds the maximum allowed size")
                    digest.update(chunk)
                    fh.write(chunk)
    except SuiteFeedError:
        raise
    except Exception as exc:
        raise SuiteFeedError(f"archive download failed ({exc})") from exc

    actual = digest.hexdigest()
    if actual != feed.sha256:
        raise SuiteFeedError(
            f"archive sha256 mismatch (expected {feed.sha256}, got {actual})"
        )


def _safe_extract(zip_path: Path, dest_dir: Path) -> None:
    """Extract after rejecting traversal, absolute and symlink members.

    Mirrors the guards in ``main._update_via_zip``: a GitHub source ZIP for
    hermes-agent never contains symlinks or paths outside its own root, so
    anything that does is a compromised or hostile mirror.
    """
    dest_real = os.path.realpath(str(dest_dir))
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for member in zf.infolist():
            name = member.filename
            if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
                raise SuiteFeedError(f"archive contains an absolute path: {name}")
            if any(part == ".." for part in name.replace("\\", "/").split("/")):
                raise SuiteFeedError(f"archive contains a parent reference: {name}")
            member_path = os.path.realpath(os.path.join(str(dest_dir), name))
            if (
                not member_path.startswith(dest_real + os.sep)
                and member_path != dest_real
            ):
                raise SuiteFeedError(
                    f"zip-slip detected: {name} escapes the extraction directory"
                )
            # Unix mode lives in the upper 16 bits of external_attr.
            mode = (member.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise SuiteFeedError(f"archive contains a symlink member: {name}")
        zf.extractall(str(dest_dir))


def _resolve_archive_root(extract_dir: Path, feed: SuiteFeed) -> Path:
    """Locate and validate the single GitHub source-archive root directory."""
    entries = [
        p for p in extract_dir.iterdir() if p.name != "__MACOSX" and not p.name.startswith(".")
    ]
    dirs = [p for p in entries if p.is_dir()]
    if len(entries) != 1 or len(dirs) != 1:
        raise SuiteFeedError(
            "archive does not contain exactly one source-archive root directory"
        )
    root = dirs[0]
    # GitHub names tag archives <repo>-<version> (leading 'v' stripped); accept
    # the un-stripped form too since that is what non-semver refs produce.
    expected = {
        f"{feed.repository_name}-{feed.version}",
        f"{feed.repository_name}-{feed.target_ref}",
    }
    if root.name not in expected:
        raise SuiteFeedError(
            f"archive root {root.name!r} does not match the trusted repository "
            f"and tag (expected one of {sorted(expected)})"
        )
    missing = [m for m in REQUIRED_MARKERS if not (root / m).exists()]
    if missing:
        raise SuiteFeedError(
            f"archive is missing expected project files: {', '.join(missing)}"
        )
    return root


def _replace_tree(source_root: Path, project_root: Path, rollback_dir: Path) -> int:
    """Swap the archive's top-level entries into the install, atomically-ish.

    Each replaced entry is moved aside into ``rollback_dir`` first, so a
    failure part-way through can put every original back. Entries in
    ``PRESERVE_ENTRIES`` are never touched, and destination-only entries are
    left alone (the archive is not authoritative about user-created files).
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
    except Exception as exc:
        for dst in reversed(created):
            try:
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst), ignore_errors=True)
                elif dst.exists() or dst.is_symlink():
                    dst.unlink()
            except Exception:
                logger.exception("Failed to remove %s during Suite update rollback", dst)
        for dst, stashed in reversed(moved):
            try:
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst), ignore_errors=True)
                elif dst.exists() or dst.is_symlink():
                    dst.unlink()
                shutil.move(str(stashed), str(dst))
            except Exception:
                logger.exception("Failed to roll back %s during Suite update", dst)
        raise SuiteFeedError(f"tree replacement failed and was rolled back ({exc})") from exc
    return replaced


def apply_suite_update(
    feed: SuiteFeed,
    project_root: Path,
    *,
    timeout: int = ARCHIVE_TIMEOUT,
) -> int:
    """Download, verify and apply ``feed`` to ``project_root``.

    Returns the number of replaced top-level entries. Raises
    ``SuiteFeedError`` on any failure, in which case ``project_root`` is left
    exactly as it was found.
    """
    project_root = Path(project_root)
    # Staged outside the install tree so a failed run never leaves partial
    # archive content behind inside it.
    tmp_dir = Path(tempfile.mkdtemp(prefix="hermes-suite-update-"))
    try:
        archive = tmp_dir / "HermesSuite.zip"
        _download_verified_archive(feed, archive, timeout)

        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        _safe_extract(archive, extract_dir)

        source_root = _resolve_archive_root(extract_dir, feed)
        return _replace_tree(source_root, project_root, tmp_dir / "rollback")
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
