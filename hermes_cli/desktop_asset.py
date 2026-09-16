"""Install the prebuilt, pipeline-signed desktop app of a release (AIS-353).

Until now every client built ``apps/desktop`` itself (``npm run pack``) and
ad-hoc-signed the result: a new code identity per update, so macOS forgot
every permission (Documents access, microphone, firewall) each time — the
boot storm of 2026-09-16. Release channels ship the Developer-ID-signed,
notarized build as a release asset ``Hermes-<version>-<platform>.zip``. This
module downloads it (sha256 and size verified against the manifest), unpacks
it exactly where the launchers expect the app
(``apps/desktop/release/<electron-builder dir>/``) and leaves a marker so
``hermes desktop --build-only`` never rebuilds — and re-signs — it locally.

git/main installs, platforms without an asset (Linux) and any download or
verification failure fall back to the local build, as before.
"""
from __future__ import annotations

import json
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from hermes_cli.release_update import (
    ARCHIVE_TIMEOUT,
    DesktopAsset,
    ReleaseFeed,
    ReleaseFeedError,
    _safe_extract,
    download_verified,
)

PREBUILT_MARKER_NAME = ".prebuilt-desktop.json"
PREBUILT_MARKER_FORMAT = "hermes-prebuilt-desktop-v1"

#: electron-builder's unpacked output directory per platform key.
RELEASE_DIRS = {
    "mac-arm64": "mac-arm64",
    "mac-x64": "mac",
    "win-x64": "win-unpacked",
    "win-arm64": "win-arm64-unpacked",
}


def desktop_platform_key(system: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    """``mac-arm64`` / ``mac-x64`` / ``win-x64`` / ``win-arm64``; ``None`` where no asset exists."""
    system = system or sys.platform
    machine = (machine or _platform.machine()).lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64", "x64"):
        arch = "x64"
    else:
        return None
    if system == "darwin":
        return f"mac-{arch}"
    if system == "win32":
        return f"win-{arch}"
    return None


def release_root(project_root: Path) -> Path:
    return Path(project_root) / "apps" / "desktop" / "release"


def prebuilt_marker_path(project_root: Path) -> Path:
    return release_root(project_root) / PREBUILT_MARKER_NAME


def read_prebuilt_marker(project_root: Path) -> Optional[dict]:
    try:
        data = json.loads(prebuilt_marker_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != PREBUILT_MARKER_FORMAT:
        return None
    return data


def expected_executable(app_dir: Path, platform_key: str) -> Path:
    if platform_key.startswith("mac"):
        return app_dir / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
    return app_dir / "Hermes.exe"


def prebuilt_desktop_current(project_root: Path, release_tag: Optional[str], platform_key: Optional[str] = None) -> bool:
    """True when the app on disk is the prebuilt one of ``release_tag`` for this platform."""
    key = platform_key or desktop_platform_key()
    if not key or not release_tag:
        return False
    marker = read_prebuilt_marker(project_root)
    if not marker or marker.get("tag") != release_tag or marker.get("platform") != key:
        return False
    return expected_executable(release_root(project_root) / RELEASE_DIRS[key], key).exists()


def _extract_asset(zip_path: Path, dest: Path, *, platform_key: str) -> None:
    """Unpack the asset. macOS uses ``ditto`` — the only tool that keeps the
    bundle's symlinks, resource forks and thereby its signature intact."""
    if platform_key.startswith("mac"):
        result = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest)], capture_output=True, text=True)
        if result.returncode != 0:
            raise ReleaseFeedError(f"ditto could not unpack {zip_path.name}: {result.stderr.strip() or result.returncode}")
        return
    _safe_extract(zip_path, dest)


def install_desktop_asset(
    feed: ReleaseFeed,
    project_root: Path,
    *,
    timeout: int = ARCHIVE_TIMEOUT,
    platform_key: Optional[str] = None,
    downloader: Optional[Callable[..., None]] = None,
    extractor: Optional[Callable[..., None]] = None,
) -> Optional[Path]:
    """Download, verify and swap in the release's desktop app.

    Returns the app directory, or ``None`` when the release has no asset for
    this platform. Raises ``ReleaseFeedError`` on any failure — the previous
    app (if any) is left in place.
    """
    key = platform_key or desktop_platform_key()
    if not key or key not in feed.desktop:
        return None
    asset: DesktopAsset = feed.desktop[key]
    root = release_root(project_root)
    target = root / RELEASE_DIRS[key]
    root.mkdir(parents=True, exist_ok=True)
    # Staged next to the target so the final rename is atomic (same volume).
    staging = root / f".staging-{key}"
    backup = root / f"{RELEASE_DIRS[key]}.previous"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="hermes-desktop-asset-"))
    try:
        zip_path = tmp_dir / asset.name
        (downloader or download_verified)(asset.url, sha256=asset.sha256, size=asset.size, dest=zip_path, timeout=timeout)
        staging.mkdir(parents=True)
        (extractor or _extract_asset)(zip_path, staging, platform_key=key)
        exe = expected_executable(staging, key)
        if not exe.exists():
            raise ReleaseFeedError(f"{asset.name} does not contain the desktop app ({exe.relative_to(staging)})")
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except OSError as exc:
            if backup.exists():
                backup.rename(target)
            raise ReleaseFeedError(f"could not install {asset.name}: {exc}") from exc
        shutil.rmtree(backup, ignore_errors=True)
        prebuilt_marker_path(project_root).write_text(
            json.dumps(
                {
                    "format": PREBUILT_MARKER_FORMAT,
                    "tag": feed.tag,
                    "version": feed.version,
                    "platform": key,
                    "name": asset.name,
                    "sha256": asset.sha256,
                    "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = [
    "PREBUILT_MARKER_FORMAT",
    "PREBUILT_MARKER_NAME",
    "RELEASE_DIRS",
    "desktop_platform_key",
    "expected_executable",
    "install_desktop_asset",
    "prebuilt_desktop_current",
    "prebuilt_marker_path",
    "read_prebuilt_marker",
]
