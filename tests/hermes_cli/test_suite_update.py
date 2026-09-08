"""Tests for the Suite-published source update feed."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli import suite_update
from hermes_cli.suite_update import SuiteFeedError

SUITE_HOST = "suite.iamds.com"
REPO = "IAMDS-GMBH/AIMDS-Agent"
PACKAGE_URL = f"https://{SUITE_HOST}/client-update/HermesSuite.zip"

MARKER_FILES = (
    "pyproject.toml",
    "run_agent.py",
    "hermes_cli/main.py",
    "hermes_cli/config.py",
)


def _config(*, base_url=f"https://{SUITE_HOST}/litellm/v1", repository=REPO, channel="prod"):
    return {
        "providers": {"aimds-suite-prod": {"base_url": base_url}},
        "updates": {"source_repository": repository, "channel": channel},
    }


def _manifest(**overrides):
    manifest = {
        "format": "hermes-source-archive-v1",
        "version": "1.2.3",
        "source_repository": REPO,
        "target_ref": "v1.2.3",
        "package_url": PACKAGE_URL,
        "sha256": "a" * 64,
        "channel": "prod",
        "build_id": "deploy-42",
        "settings": {},
    }
    manifest.update(overrides)
    return manifest


def _validate(manifest, *, current_version="1.0.0", channel="prod", host=SUITE_HOST):
    return suite_update.validate_manifest(
        manifest,
        trusted_repository=REPO,
        suite_host=host,
        channel=channel,
        current_version=current_version,
    )


def _build_archive(root_name="AIMDS-Agent-1.2.3", *, extra=None, members=None):
    """Build a GitHub-style source archive in memory; returns (bytes, sha256)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if members is not None:
            for name, mode in members:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                zf.writestr(info, "payload")
        else:
            for marker in MARKER_FILES:
                zf.writestr(f"{root_name}/{marker}", f"# {marker} v1.2.3\n")
            zf.writestr(f"{root_name}/docs/README.md", "new docs\n")
            for name, content in (extra or {}).items():
                zf.writestr(f"{root_name}/{name}", content)
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def _feed(sha256, version="1.2.3"):
    return suite_update.SuiteFeed(
        version=version,
        target_ref=f"v{version}",
        package_url=PACKAGE_URL,
        sha256=sha256,
        repository=REPO,
    )


def _install_tree(tmp_path: Path) -> Path:
    """A source install with user-owned paths that must survive an update."""
    root = tmp_path / "hermes-agent"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "docs").mkdir()
    for marker in MARKER_FILES:
        (root / marker).write_text("# old\n")
    (root / "docs" / "README.md").write_text("old docs\n")
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("binary")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (root / ".env").write_text("SECRET=keep-me")
    return root


def _snapshot(root: Path) -> dict:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _urlopen_returning(data: bytes):
    return lambda *a, **k: _FakeResponse(data)


# =========================================================================
# Host and repository resolution
# =========================================================================

def test_suite_host_ignores_base_url_path():
    """A provider base_url with a path still normalizes to the bare host."""
    assert suite_update.resolve_suite_host(_config()) == SUITE_HOST


def test_suite_host_keeps_explicit_port():
    config = _config(base_url=f"https://{SUITE_HOST}:8443/litellm/v1")
    assert suite_update.resolve_suite_host(config) == f"{SUITE_HOST}:8443"


def test_suite_host_rejects_non_https():
    config = _config(base_url=f"http://{SUITE_HOST}/litellm/v1")
    assert suite_update.resolve_suite_host(config) is None


def test_trusted_repository_falls_back_to_git_origin():
    config = _config(repository="")
    origin = f"https://github.com/{REPO}.git"
    assert suite_update.resolve_trusted_repository(config, origin) == REPO


def test_trusted_repository_parses_scp_style_origin():
    config = _config(repository="")
    assert (
        suite_update.resolve_trusted_repository(config, f"git@github.com:{REPO}.git")
        == REPO
    )


def test_trusted_repository_none_without_origin():
    assert suite_update.resolve_trusted_repository(_config(repository=""), None) is None


# =========================================================================
# Manifest validation
# =========================================================================

def test_valid_manifest_accepted():
    feed = _validate(_manifest())
    assert (feed.version, feed.target_ref, feed.repository) == ("1.2.3", "v1.2.3", REPO)


def test_manifest_ignores_unknown_fields():
    assert _validate(_manifest(future_field={"x": 1})).version == "1.2.3"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"format": "hermes-source-archive-v2"}, "format"),
        ({"source_repository": "attacker/hermes-agent"}, "trusted repository"),
        ({"target_ref": "main"}, "vX.Y.Z"),
        ({"version": "9.9.9"}, "does not match target_ref"),
        ({"channel": "staging"}, "channel"),
        ({"package_url": f"http://{SUITE_HOST}/client-update/HermesSuite.zip"}, "HTTPS"),
        ({"package_url": "https://evil.example.com/HermesSuite.zip"}, "Suite"),
        ({"package_url": f"https://{SUITE_HOST}:8443/HermesSuite.zip"}, "Suite"),
        ({"sha256": "A" * 64}, "sha256"),
        ({"sha256": "abc"}, "sha256"),
        ({"settings": []}, "settings"),
        ({"build_id": 42}, "build_id"),
    ],
)
def test_manifest_rejections(overrides, reason):
    with pytest.raises(SuiteFeedError, match=reason):
        _validate(_manifest(**overrides))


def test_manifest_rejects_non_newer_version():
    with pytest.raises(SuiteFeedError, match="not newer"):
        _validate(_manifest(), current_version="1.2.3")


def test_check_suite_update_returns_none_when_not_newer():
    with patch.object(suite_update, "fetch_manifest", return_value=_manifest()):
        assert suite_update.check_suite_update(_config(), None, "2.0.0") is None


def test_check_suite_update_returns_none_when_feed_unreachable():
    with patch.object(
        suite_update.urllib.request, "urlopen", side_effect=OSError("no route")
    ):
        assert suite_update.check_suite_update(_config(), None, "1.0.0") is None


def test_check_suite_update_returns_none_on_malformed_json():
    with patch.object(
        suite_update.urllib.request, "urlopen", _urlopen_returning(b"not json{")
    ):
        assert suite_update.check_suite_update(_config(), None, "1.0.0") is None


def test_check_suite_update_returns_feed():
    body = json.dumps(_manifest()).encode()
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(body)):
        feed = suite_update.check_suite_update(_config(), None, "1.0.0")
    assert feed is not None and feed.version == "1.2.3"


# =========================================================================
# Archive application
# =========================================================================

def test_successful_update_replaces_tree_and_preserves_user_paths(tmp_path):
    root = _install_tree(tmp_path)
    data, digest = _build_archive()
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        replaced = suite_update.apply_suite_update(_feed(digest), root)

    assert replaced > 0
    assert (root / "pyproject.toml").read_text() == "# pyproject.toml v1.2.3\n"
    assert (root / "docs" / "README.md").read_text() == "new docs\n"
    assert (root / ".env").read_text() == "SECRET=keep-me"
    assert (root / "venv" / "bin" / "python").exists()
    assert (root / "node_modules" / "pkg").is_dir()
    assert (root / ".git" / "HEAD").exists()


def test_checksum_mismatch_leaves_tree_untouched(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, _ = _build_archive()
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(SuiteFeedError, match="sha256 mismatch"):
            suite_update.apply_suite_update(_feed("b" * 64), root)
    assert _snapshot(root) == before


def test_wrong_archive_root_rejected(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive(root_name="not-hermes-1.2.3")
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(SuiteFeedError, match="archive root"):
            suite_update.apply_suite_update(_feed(digest), root)
    assert _snapshot(root) == before


def test_missing_project_markers_rejected(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("AIMDS-Agent-1.2.3/pyproject.toml", "x")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(SuiteFeedError, match="missing expected project files"):
            suite_update.apply_suite_update(_feed(digest), root)
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "members, reason",
    [
        ([("AIMDS-Agent-1.2.3/../escape.py", 0o100644)], "parent reference"),
        ([("/etc/passwd", 0o100644)], "absolute path"),
        ([("AIMDS-Agent-1.2.3/link", 0o120777)], "symlink"),
    ],
)
def test_unsafe_archive_members_rejected(tmp_path, members, reason):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive(members=members)
    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(SuiteFeedError, match=reason):
            suite_update.apply_suite_update(_feed(digest), root)
    assert _snapshot(root) == before


def test_failed_replacement_rolls_back_every_entry(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive()

    real_copytree = suite_update.shutil.copytree
    calls = {"n": 0}

    def flaky_copytree(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_copytree(*a, **k)

    with patch.object(suite_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with patch.object(suite_update.shutil, "copytree", flaky_copytree):
            with pytest.raises(SuiteFeedError, match="rolled back"):
                suite_update.apply_suite_update(_feed(digest), root)

    assert _snapshot(root) == before
