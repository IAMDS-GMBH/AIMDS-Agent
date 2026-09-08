"""Tests for the release-archive updater (AIS-312): manifest, download, apply, rollback."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli import release_update
from hermes_cli.release_channels import RELEASE_REPO, release_download_url
from hermes_cli.release_update import ReleaseFeedError

MARKER_FILES = (
    "pyproject.toml",
    "run_agent.py",
    "hermes_cli/main.py",
    "hermes_cli/config.py",
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _manifest(**overrides):
    manifest = {
        "format": "hermes-release-v1",
        "version": "1.2.3",
        "tag": "v1.2.3",
        "commit_sha": COMMIT,
        "source_archive": "hermes-source-1.2.3.zip",
        "sha256": "a" * 64,
        "size": 1234,
        "built_at": "2026-09-08T10:00:00+00:00",
    }
    manifest.update(overrides)
    return manifest


def _validate(manifest, *, channel="stable", **kw):
    return release_update.validate_manifest(manifest, channel=channel, **kw)


def _build_archive(root_name="hermes-agent-1.2.3", *, extra=None, members=None):
    """Build a `git archive`-style zip in memory; returns (bytes, sha256)."""
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


def _feed(sha256, *, size=None, version="1.2.3", channel="stable"):
    return release_update.ReleaseFeed(
        version=version,
        tag=f"v{version}",
        commit_sha=COMMIT,
        package_url=release_download_url(f"v{version}", f"hermes-source-{version}.zip"),
        sha256=sha256,
        size=size or 0,
        build_id="2026-09-08T10:00:00+00:00",
        channel=channel,
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
    (root / ".hermes-release.json").write_text('{"format": "hermes-release-marker-v1"}')
    return root


def _snapshot(root: Path) -> dict:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class _FakeResponse(io.BytesIO):
    def __init__(self, data, url=""):
        super().__init__(data)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _urlopen_returning(data: bytes, seen=None):
    def _open(request, *a, **k):
        url = getattr(request, "full_url", request)
        if seen is not None:
            seen.append(url)
        return _FakeResponse(data, url)

    return _open


# =========================================================================
# Manifest validation (keep in sync with build_source_package.sh + desktop)
# =========================================================================

def test_valid_stable_manifest_accepted_on_both_channels():
    for channel in ("stable", "preview"):
        feed = _validate(_manifest(), channel=channel)
        assert (feed.version, feed.tag, feed.commit_sha, feed.size) == ("1.2.3", "v1.2.3", COMMIT, 1234)
        assert feed.package_url == f"https://github.com/{RELEASE_REPO}/releases/download/v1.2.3/hermes-source-1.2.3.zip"
        assert feed.channel == channel
        assert feed.build_id == "2026-09-08T10:00:00+00:00"


def test_candidate_manifest_accepted_on_preview_only():
    rc = _manifest(version="1.2.3-rc.2", tag="v1.2.3-rc.2", source_archive="hermes-source-1.2.3-rc.2.zip")
    assert _validate(rc, channel="preview").tag == "v1.2.3-rc.2"
    with pytest.raises(ReleaseFeedError, match="not a stable release"):
        _validate(rc, channel="stable")


@pytest.mark.parametrize("channel", ["prod", "main", "staging"])
def test_manifest_rejected_for_non_release_channels(channel):
    with pytest.raises(ReleaseFeedError, match="is not a"):
        _validate(_manifest(), channel=channel)


def test_manifest_ignores_unknown_fields():
    assert _validate(_manifest(future_field={"x": 1})).version == "1.2.3"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"format": "hermes-source-archive-v1"}, "format"),
        ({"tag": "main"}, "not a release tag"),
        ({"version": "9.9.9"}, "does not match tag"),
        ({"commit_sha": COMMIT[:-1]}, "commit_sha"),
        ({"commit_sha": COMMIT.upper()}, "commit_sha"),
        ({"sha256": "A" * 64}, "sha256"),
        ({"sha256": "abc"}, "sha256"),
        ({"source_archive": "hermes-source-1.2.4.zip"}, "source_archive"),
        ({"source_archive": "../hermes-source-1.2.3.zip"}, "source_archive"),
        ({"source_archive": f"https://github.com/{RELEASE_REPO}/releases/download/v1.2.3/hermes-source-1.2.3.zip"}, "source_archive"),
        ({"size": 0}, "size"),
        ({"size": "1234"}, "size"),
        ({"size": True}, "size"),
        ({"built_at": "yesterday"}, "built_at"),
        ({"built_at": 42}, "built_at"),
    ],
)
def test_manifest_rejections(overrides, reason):
    with pytest.raises(ReleaseFeedError, match=reason):
        _validate(_manifest(**overrides))


def test_manifest_must_match_the_release_it_was_downloaded_from():
    with pytest.raises(ReleaseFeedError, match="does not match release"):
        _validate(_manifest(), channel="preview", release_tag="v1.2.4-rc.1")
    assert _validate(_manifest(), channel="preview", release_tag="v1.2.3").tag == "v1.2.3"


# =========================================================================
# Manifest resolution
# =========================================================================

def test_fetch_release_feed_stable_uses_static_latest_url():
    seen = []
    body = json.dumps(_manifest()).encode()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(body, seen)):
        feed = release_update.fetch_release_feed("stable")
    assert feed.tag == "v1.2.3"
    assert seen == [f"https://github.com/{RELEASE_REPO}/releases/latest/download/hermes-release.json"]


def test_fetch_release_feed_preview_uses_api_and_manifest_asset():
    seen = []
    body = json.dumps(_manifest(version="1.2.4-rc.1", tag="v1.2.4-rc.1", source_archive="hermes-source-1.2.4-rc.1.zip")).encode()
    release = {
        "tag_name": "v1.2.4-rc.1",
        "assets": [
            {"name": "HermesSetup.dmg", "browser_download_url": "https://github.com/x/dmg"},
            {"name": "hermes-release.json", "browser_download_url": f"https://github.com/{RELEASE_REPO}/releases/download/v1.2.4-rc.1/hermes-release.json"},
        ],
    }
    with patch.object(release_update, "fetch_release_via_api", return_value=release) as api, \
         patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(body, seen)):
        feed = release_update.fetch_release_feed("preview")
    assert feed.tag == "v1.2.4-rc.1" and feed.channel == "preview"
    assert api.call_args.args == ("preview",) and api.call_args.kwargs["repo"] == RELEASE_REPO
    assert seen == [f"https://github.com/{RELEASE_REPO}/releases/download/v1.2.4-rc.1/hermes-release.json"]


@pytest.mark.parametrize(
    "setup, reason",
    [
        ({"api": None}, "no preview release"),
        ({"api": {"tag_name": "v1.2.4-rc.1", "assets": []}}, "has no hermes-release.json"),
        ({"api": {"tag_name": "v1.2.4-rc.1", "assets": [{"name": "hermes-release.json", "browser_download_url": "https://github.com/m"}]}, "body": b"not json{"}, "not valid JSON"),
        ({"api": {"tag_name": "v9.9.9", "assets": [{"name": "hermes-release.json", "browser_download_url": "https://github.com/m"}]}}, "does not match release"),
    ],
)
def test_fetch_release_feed_preview_never_returns_none(setup, reason):
    body = setup.get("body") or json.dumps(_manifest()).encode()
    with patch.object(release_update, "fetch_release_via_api", return_value=setup["api"]), \
         patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(body)):
        with pytest.raises(ReleaseFeedError, match=reason):
            release_update.fetch_release_feed("preview")


def test_fetch_release_feed_stable_unreachable_or_candidate_rejected():
    with patch.object(release_update.urllib.request, "urlopen", side_effect=OSError("404")):
        with pytest.raises(ReleaseFeedError, match="unreachable"):
            release_update.fetch_release_feed("stable")
    rc = json.dumps(_manifest(version="1.2.4-rc.1", tag="v1.2.4-rc.1", source_archive="hermes-source-1.2.4-rc.1.zip")).encode()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(rc)):
        with pytest.raises(ReleaseFeedError, match="not a stable release"):
            release_update.fetch_release_feed("stable")


def test_fetch_release_feed_rejects_branch_channels():
    with pytest.raises(ReleaseFeedError, match="not a release channel"):
        release_update.fetch_release_feed("main")


# =========================================================================
# classify_feed
# =========================================================================

def _marker(tag="v1.2.3", commit=COMMIT):
    return {"format": "hermes-release-marker-v1", "channel": "stable", "tag": tag, "version": tag[1:], "commit_sha": commit}


@pytest.mark.parametrize(
    "feed_version, marker, current, expected",
    [
        ("1.2.3", _marker(), "0.0.0", "same"),                              # same commit
        ("1.2.3", _marker(tag="v1.2.3-rc.2", commit="f" * 40), "0.0.0", "newer"),  # promoted, other commit
        ("1.2.4", _marker(tag="v1.2.3", commit="f" * 40), "0.0.0", "newer"),
        ("1.2.2", _marker(tag="v1.2.3", commit="f" * 40), "9.9.9", "older"),
        ("1.2.3", _marker(commit="f" * 40), "0.0.0", "newer"),            # same tag, different commit
        ("1.2.3", None, "1.2.3", "same"),
        ("1.2.4", None, "1.2.3", "newer"),
        ("1.2.2", None, "1.2.3", "older"),
        ("1.2.3", None, "1.2.3-rc.1", "newer"),                             # stable above its own rc
        ("1.2.3", None, "0.0.0-dev", "newer"),                              # unparsable version
    ],
)
def test_classify_feed(feed_version, marker, current, expected):
    feed = _feed("a" * 64, version=feed_version)
    assert release_update.classify_feed(feed, marker=marker, current_version=current) == expected


# =========================================================================
# Archive application
# =========================================================================

def test_successful_update_replaces_tree_and_preserves_user_paths(tmp_path):
    root = _install_tree(tmp_path)
    data, digest = _build_archive()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        replaced = release_update.apply_release_update(_feed(digest, size=len(data)), root)

    assert replaced > 0
    assert (root / "pyproject.toml").read_text() == "# pyproject.toml v1.2.3\n"
    assert (root / "docs" / "README.md").read_text() == "new docs\n"
    assert (root / ".env").read_text() == "SECRET=keep-me"
    assert (root / "venv" / "bin" / "python").exists()
    assert (root / "node_modules" / "pkg").is_dir()
    assert (root / ".git" / "HEAD").exists()
    assert (root / ".hermes-release.json").read_text().startswith('{"format"')


def test_archive_root_name_is_irrelevant(tmp_path):
    root = _install_tree(tmp_path)
    data, digest = _build_archive(root_name="AIMDS-Agent-1.2.3")
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        assert release_update.apply_release_update(_feed(digest, size=len(data)), root) > 0
    assert (root / "docs" / "README.md").read_text() == "new docs\n"


def test_checksum_mismatch_leaves_tree_untouched(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, _ = _build_archive()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match="sha256 mismatch"):
            release_update.apply_release_update(_feed("b" * 64, size=len(data)), root)
    assert _snapshot(root) == before


def test_size_mismatch_leaves_tree_untouched(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match="size mismatch"):
            release_update.apply_release_update(_feed(digest, size=len(data) + 1), root)
    assert _snapshot(root) == before


def test_download_failure_is_a_feed_error(tmp_path):
    root = _install_tree(tmp_path)
    with patch.object(release_update.urllib.request, "urlopen", side_effect=OSError("reset")):
        with pytest.raises(ReleaseFeedError, match="download failed"):
            release_update.apply_release_update(_feed("a" * 64), root)


def test_two_roots_rejected(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for marker in MARKER_FILES:
            zf.writestr(f"a/{marker}", "x")
        zf.writestr("b/README.md", "x")
    data = buffer.getvalue()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match="exactly one"):
            release_update.apply_release_update(_feed(hashlib.sha256(data).hexdigest(), size=len(data)), root)
    assert _snapshot(root) == before


def test_missing_project_markers_rejected(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("hermes-agent-1.2.3/pyproject.toml", "x")
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match="missing expected project files"):
            release_update.apply_release_update(_feed(digest, size=len(data)), root)
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "members, reason",
    [
        ([("hermes-agent-1.2.3/../escape.py", 0o100644)], "parent reference"),
        ([("/etc/passwd", 0o100644)], "absolute path"),
        ([("hermes-agent-1.2.3/link", 0o120777)], "symlink"),
    ],
)
def test_unsafe_archive_members_rejected(tmp_path, members, reason):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive(members=members)
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match=reason):
            release_update.apply_release_update(_feed(digest, size=len(data)), root)
    assert _snapshot(root) == before


def _flaky_copytree(fail_on=2):
    real_copytree = release_update.shutil.copytree
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise OSError("disk full")
        return real_copytree(*a, **k)

    return flaky


def test_failed_replacement_rolls_back_every_entry(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive()
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with patch.object(release_update.shutil, "copytree", _flaky_copytree()):
            with pytest.raises(ReleaseFeedError, match="rolled back"):
                release_update.apply_release_update(_feed(digest, size=len(data)), root)
    assert _snapshot(root) == before


def test_failed_replacement_removes_new_entries_on_rollback(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive(extra={"aaa-new-entry/data.txt": "new\n"})
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with patch.object(release_update.shutil, "copytree", _flaky_copytree()):
            with pytest.raises(ReleaseFeedError, match="rolled back"):
                release_update.apply_release_update(_feed(digest, size=len(data)), root)
    assert _snapshot(root) == before
    assert not (root / "aaa-new-entry").exists()


def test_verify_failure_rolls_back(tmp_path):
    root = _install_tree(tmp_path)
    before = _snapshot(root)
    data, digest = _build_archive()
    seen = []
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        with pytest.raises(ReleaseFeedError, match="failed verification"):
            release_update.apply_release_update(
                _feed(digest, size=len(data)), root, verify=lambda p: seen.append(p) or False
            )
    assert seen == [root]
    assert _snapshot(root) == before


def test_temp_dir_removed_after_apply(tmp_path, monkeypatch):
    root = _install_tree(tmp_path)
    data, digest = _build_archive()
    made = []
    real_mkdtemp = release_update.tempfile.mkdtemp

    def recording_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        made.append(d)
        return d

    monkeypatch.setattr(release_update.tempfile, "mkdtemp", recording_mkdtemp)
    with patch.object(release_update.urllib.request, "urlopen", _urlopen_returning(data)):
        release_update.apply_release_update(_feed(digest, size=len(data)), root)
    assert made and not Path(made[0]).exists()


def test_is_source_tree(tmp_path):
    assert release_update.is_source_tree(_install_tree(tmp_path))
    assert not release_update.is_source_tree(tmp_path)
