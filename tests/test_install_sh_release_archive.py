"""AIS-313: the install scripts install from the public release repository.

Text contracts on scripts/install.sh and scripts/install.ps1 — the scripts
are exercised end-to-end manually (they download 20+ MB), so these tests pin
the pieces that must not regress: the release repository, the verification
and marker steps, the channel/tag flags, and the absence of source-repository
archive downloads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"

RELEASE_REPO = "IAMDS-GMBH/AIMDS-Agent-Releases"


@pytest.fixture(scope="module")
def sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_install_sh_targets_the_release_repository(sh: str) -> None:
    assert f'RELEASE_REPO="{RELEASE_REPO}"' in sh
    assert 'RELEASE_MANIFEST_ASSET="hermes-release.json"' in sh
    assert 'RELEASE_MARKER_FILE=".hermes-release.json"' in sh
    # stable via the redirecting /latest/download URL, preview via the API, tag directly
    assert "releases/latest/download/$RELEASE_MANIFEST_ASSET" in sh
    assert 'api.github.com/repos/$RELEASE_REPO/releases?per_page=30' in sh
    assert "releases/download/$INSTALL_TAG/$RELEASE_MANIFEST_ASSET" in sh


def test_install_sh_verifies_and_marks_the_archive(sh: str) -> None:
    assert 'if [ "$actual" != "$RELEASE_SHA256" ]; then' in sh
    assert '"format": "hermes-release-marker-v1"' in sh
    for key in ("channel", "tag", "version", "commit_sha", "sha256", "applied_at"):
        assert f'"{key}": "%s"' in sh, key
    assert 'RELEASE_ARCHIVE" != "hermes-source-$RELEASE_VERSION.zip"' in sh
    # existing installs keep the user's environment
    for entry in ("venv", ".venv", "node_modules", ".git", ".env", ".hermes-release.json"):
        assert entry in re.search(r"release_preserve_entry\(\) \{.*?\n\}", sh, re.S).group(0), entry


def test_install_sh_channel_and_tag_flags(sh: str) -> None:
    assert "--tag|-Tag)" in sh
    assert 'stable|tags) CHANNEL="stable" ;;' in sh
    assert 'preview) CHANNEL="preview" ;;' in sh
    assert "resolve_install_source" in sh and "install_release_archive" in sh
    # the repository stage routes to the archive before any git logic
    assert sh.index('if [ "$INSTALL_SOURCE" = "release" ]; then') < sh.index("resolve_install_ref\n\n    # An interrupted previous clone")


def test_install_sh_version_stamp_prefers_the_marker(sh: str) -> None:
    assert 'if [ -f "$INSTALL_DIR/$RELEASE_MARKER_FILE" ]; then' in sh
    assert "Syncing version from release $_release_tag" in sh
    assert 'echo "release" > "$HERMES_HOME/.install_method"' in sh


def test_install_ps1_targets_the_release_repository(ps1: str) -> None:
    assert f'$ReleaseRepo = "{RELEASE_REPO}"' in ps1
    assert 'function Install-ReleaseArchive' in ps1
    assert 'function Get-ReleaseManifest' in ps1
    assert 'Get-FileHash -Algorithm SHA256' in ps1
    assert '"hermes-release-marker-v1"' in ps1
    # no BOM: hermes_cli/release_marker.py parses strict UTF-8 JSON
    assert 'New-Object System.Text.UTF8Encoding($false)' in ps1


def test_install_ps1_no_longer_downloads_source_repository_archives(ps1: str) -> None:
    assert "github.com/IAMDS-GMBH/AIMDS-Agent/archive/" not in ps1
    assert "raw.githubusercontent.com" not in ps1
    # git clone of the source repository stays for developers only
    assert '$RepoUrlHttps = "https://github.com/IAMDS-GMBH/AIMDS-Agent.git"' in ps1
    assert 'if ($script:InstallSource -eq "release") {' in ps1


def test_install_scripts_agree_on_the_preserve_set(sh: str, ps1: str) -> None:
    sh_set = set(re.findall(r"[A-Za-z0-9_.-]+", re.search(r"^\s+(venv\|\.venv[^)]*)\)", sh, re.M).group(1)))
    ps_fn = re.search(r"function Test-ReleasePreserveEntry \{.*?\n\}", ps1, re.S).group(0)
    ps_set = set(re.findall(r'"([^"]+)"', re.search(r"return @\((.*?)\) -contains \$Name", ps_fn, re.S).group(1)))
    assert sh_set == ps_set
    assert {"venv", ".venv", "node_modules", ".git", ".env", ".worktrees", ".hermes-release.json"} <= sh_set
