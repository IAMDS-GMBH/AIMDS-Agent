"""Release-archive update source (AIS-312): source resolution, apply/check paths, fallback chain, banner, dashboard."""

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import main as hermes_main
from hermes_cli.release_channels import RELEASE_REPO, release_download_url
from hermes_cli.release_marker import read_release_marker, write_release_marker
from hermes_cli.release_update import ReleaseFeed, ReleaseFeedError

COMMIT = "0123456789abcdef0123456789abcdef01234567"
MARKER_FILES = ("pyproject.toml", "run_agent.py", "hermes_cli/main.py", "hermes_cli/config.py")


# ---------------------------------------------------------------------------
# fixtures (same conventions as test_cmd_update.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_managed_uv():
    import shutil

    with patch("hermes_cli.managed_uv.resolve_uv", side_effect=lambda: shutil.which("uv")), \
         patch("hermes_cli.managed_uv.ensure_uv", side_effect=lambda: shutil.which("uv")), \
         patch("hermes_cli.managed_uv.update_managed_uv", side_effect=lambda: None):
        yield


@pytest.fixture(autouse=True)
def _never_sync_real_skills(monkeypatch):
    monkeypatch.setattr(
        "tools.skills_sync.sync_skills",
        lambda quiet=True: {
            "copied": [], "updated": [], "skipped": 0, "restored": [],
            "user_modified": [], "cleaned": [], "suppressed": [], "total_bundled": 0,
            "optional_provenance_backfilled": [],
        },
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A sandboxed PROJECT_ROOT that looks like a source tree (no .git)."""
    project = tmp_path / "hermes-agent"
    (project / "hermes_cli").mkdir(parents=True)
    for marker in MARKER_FILES:
        (project / marker).write_text("# tree\n")
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", project)
    return project


def _feed(version="9.9.9", *, commit=COMMIT, channel="stable", size=4096):
    return ReleaseFeed(
        version=version,
        tag=f"v{version}",
        commit_sha=commit,
        package_url=release_download_url(f"v{version}", f"hermes-source-{version}.zip"),
        sha256="c" * 64,
        size=size,
        build_id="2026-09-08T10:00:00+00:00",
        channel=channel,
    )


def _marker(root, *, tag="v1.0.0", commit="a" * 40, channel="stable"):
    write_release_marker(root, channel=channel, tag=tag, version=tag[1:], commit_sha=commit, sha256="b" * 64)


def _config(source="auto", channel="auto"):
    return {"updates": {"source": source, "channel": channel}}


def _git_side_effect(calls, *, ls_remote_rc=0, ls_remote_exc=None, branch="main", commit_count="0"):
    def side_effect(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        calls.append((joined, kwargs))
        if "ls-remote" in joined:
            if ls_remote_exc is not None:
                raise ls_remote_exc
            return subprocess.CompletedProcess(cmd, ls_remote_rc, stdout="", stderr="")
        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{branch}\n", stderr="")
        if joined.endswith("tag --list") or joined.endswith("tag --points-at HEAD"):
            return subprocess.CompletedProcess(cmd, 0, stdout="v1.0.0\n", stderr="")
        if "rev-parse" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha\n", stderr="")
        if "rev-list" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{commit_count}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return side_effect


# ---------------------------------------------------------------------------
# _resolve_update_source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("configured, expected", [("release", ("release", True)), ("git", ("git", True)), ("RELEASE", ("release", True))])
def test_resolve_update_source_honours_pinned_config(root, configured, expected):
    with patch("hermes_cli.config.load_config", return_value=_config(source=configured)), \
         patch("hermes_cli.main.subprocess.run") as run:
        assert hermes_main._resolve_update_source(SimpleNamespace()) == expected
    run.assert_not_called()


def test_resolve_update_source_auto_marker_means_release(root):
    (root / ".git").mkdir()
    _marker(root)
    with patch("hermes_cli.config.load_config", return_value=_config()), \
         patch("hermes_cli.main.subprocess.run") as run:
        assert hermes_main._resolve_update_source(SimpleNamespace()) == ("release", False)
    run.assert_not_called()


def test_resolve_update_source_auto_without_git(root, tmp_path):
    with patch("hermes_cli.config.load_config", return_value=_config()):
        assert hermes_main._resolve_update_source(SimpleNamespace()) == ("release", False)
        assert hermes_main._resolve_update_source(SimpleNamespace(), project_root=tmp_path / "empty") == ("pip", False)


def test_resolve_update_source_auto_git_probe(root, capsys):
    (root / ".git").mkdir()
    calls = []
    with patch("hermes_cli.config.load_config", return_value=_config()), \
         patch("hermes_cli.main.subprocess.run", side_effect=_git_side_effect(calls)):
        assert hermes_main._resolve_update_source(SimpleNamespace()) == ("git", False)
    joined, kwargs = calls[-1]
    assert joined == "git ls-remote --exit-code origin HEAD"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0" and kwargs["env"]["GCM_INTERACTIVE"] == "never"
    assert kwargs["timeout"] == 15

    for failure in ({"ls_remote_rc": 128}, {"ls_remote_exc": subprocess.TimeoutExpired("git", 15)}, {"ls_remote_exc": OSError("no git")}):
        with patch("hermes_cli.config.load_config", return_value=_config()), \
             patch("hermes_cli.main.subprocess.run", side_effect=_git_side_effect([], **failure)):
            assert hermes_main._resolve_update_source(SimpleNamespace()) == ("release", False)
    assert "origin is not reachable" in capsys.readouterr().out


def test_resolve_update_source_explicit_flag_wins(root):
    with patch("hermes_cli.config.load_config", return_value=_config(source="git")):
        assert hermes_main._resolve_update_source(SimpleNamespace(source="release")) == ("release", True)


# ---------------------------------------------------------------------------
# _resolve_update_branch with the release source
# ---------------------------------------------------------------------------

def test_resolve_update_branch_release_auto_uses_marker_channel_or_stable(root):
    with patch("hermes_cli.config.load_config", return_value=_config()):
        assert hermes_main._resolve_update_branch(SimpleNamespace(), source="release") == "stable"
        _marker(root, channel="preview")
        assert hermes_main._resolve_update_branch(SimpleNamespace(), source="release") == "preview"
        _marker(root, channel="main")
        assert hermes_main._resolve_update_branch(SimpleNamespace(), source="release") == "stable"
        assert hermes_main._resolve_update_branch(SimpleNamespace(branch="main"), source="release") == "main"
    with patch("hermes_cli.config.load_config", return_value=_config(channel="preview")):
        assert hermes_main._resolve_update_branch(SimpleNamespace(), source="release") == "preview"


# ---------------------------------------------------------------------------
# hermes update --check
# ---------------------------------------------------------------------------

def _run_check(branch, *, source="release", forced=False, branch_explicit=False):
    try:
        hermes_main._cmd_update_check(branch=branch, branch_explicit=branch_explicit, source=source, forced=forced)
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize(
    "marker_kw, feed_version, expected",
    [
        (dict(tag="v1.0.0", commit="a" * 40), "9.9.9", "Update available: v9.9.9 (release archive, 0123456789). Currently v1.0.0."),
        (dict(tag="v9.9.9", commit=COMMIT), "9.9.9", "Already up to date."),
        (dict(tag="v9.9.9-rc.1", commit="f" * 40), "9.9.8", "On v9.9.9-rc.1, newer than stable v9.9.8 — nothing to do."),
    ],
)
def test_update_check_release_states(root, capsys, marker_kw, feed_version, expected):
    _marker(root, **marker_kw)
    with patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed(feed_version)), \
         patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.main.subprocess.run") as run:
        assert _run_check("stable") is None
    run.assert_not_called()
    assert expected in capsys.readouterr().out


def test_update_check_release_hint_includes_branch_when_explicit(root, capsys):
    with patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9", channel="preview")), \
         patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.config.recommended_update_command", return_value="hermes update"):
        _run_check("preview", branch_explicit=True)
    assert "Run 'hermes update --branch preview' to install." in capsys.readouterr().out


def test_update_check_release_manifest_error_forced_exits(root, capsys):
    with patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("404")), \
         patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.main.subprocess.run") as run:
        assert _run_check("stable", forced=True) == 1
    run.assert_not_called()
    assert "✗ Could not read the stable release manifest: 404" in capsys.readouterr().out


def test_update_check_release_manifest_error_auto_falls_back_to_git_check(root, capsys):
    (root / ".git").mkdir()
    calls = []

    def side_effect(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        calls.append(joined)
        if joined.endswith("tag --list"):
            return subprocess.CompletedProcess(cmd, 0, stdout="v1.0.0\n", stderr="")
        if joined.endswith("tag --points-at HEAD"):
            return subprocess.CompletedProcess(cmd, 0, stdout="v1.0.0\n", stderr="")
        if "rev-parse" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")

    with patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("404")), \
         patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.main.subprocess.run", side_effect=side_effect):
        assert _run_check("stable") is None
    out = capsys.readouterr().out
    assert "⚠ Release manifest unavailable: 404" in out
    assert "Checking the previous update path" in out
    assert any("fetch --force --tags origin" in c for c in calls), calls
    assert "Already up to date." in out


def test_update_check_release_runs_before_the_pypi_branch(root, capsys):
    """A source tree without .git and without a marker is not a PyPI install."""
    with patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9")), \
         patch("hermes_cli.config.detect_install_method", return_value="pip"), \
         patch("hermes_cli.banner.check_via_pypi") as pypi:
        assert _run_check("stable") is None
    pypi.assert_not_called()
    assert "Update available: v9.9.9 (release archive" in capsys.readouterr().out


def test_update_check_release_main_channel(root, capsys):
    with patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.release_update.fetch_release_feed") as fetch:
        assert _run_check("main", forced=True) == 1
        assert "needs a git checkout" in capsys.readouterr().out
        fetch.assert_not_called()
        # auto: skipped silently, git check reports the missing .git as today
        assert _run_check("main") == 1
        assert "Not a git repository" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# hermes update — release apply path
# ---------------------------------------------------------------------------

def _args(**kw):
    base = dict(branch="stable", check=False, yes=True, no_backup=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _run_update(args=None):
    try:
        hermes_main.cmd_update(args or _args())
    except SystemExit as exc:
        return exc.code
    return None


def test_update_release_applies_and_writes_marker(root, capsys):
    _marker(root, tag="v1.0.0", commit="a" * 40)
    feed = _feed("9.9.9")
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=feed) as fetch, \
         patch("hermes_cli.release_update.apply_release_update", return_value=7) as apply_, \
         patch("hermes_cli.main._create_pre_update_snapshot", return_value="snap-1") as snap, \
         patch("hermes_cli.main._run_post_update_pipeline") as pipeline, \
         patch("hermes_cli.main.subprocess.run") as run:
        assert _run_update() is None
    run.assert_not_called()
    fetch.assert_called_once_with("stable")
    apply_.assert_called_once()
    assert apply_.call_args.args[:2] == (feed, root)
    assert callable(apply_.call_args.kwargs["verify"])
    snap.assert_called_once()
    pipeline.assert_called_once_with(gateway_mode=False, assume_yes=True, pre_update_snapshot_id="snap-1", code_updated_line="✓ Code updated to v9.9.9!")
    marker = read_release_marker(root)
    assert (marker["tag"], marker["version"], marker["commit_sha"], marker["sha256"], marker["channel"]) == ("v9.9.9", "9.9.9", COMMIT, "c" * 64, "stable")
    assert marker["build_id"] == "2026-09-08T10:00:00+00:00"
    out = capsys.readouterr().out
    assert f"Checking {RELEASE_REPO} releases (stable channel)" in out
    assert "Replaced 7 top-level entries from v9.9.9" in out


def test_update_release_runs_shared_post_update_pipeline_once(root, capsys):
    _marker(root, tag="v1.0.0", commit="a" * 40)
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9")), \
         patch("hermes_cli.release_update.apply_release_update", return_value=7), \
         patch("hermes_cli.main._create_pre_update_snapshot", return_value="snap-1"), \
         patch("hermes_cli.main._clear_bytecode_cache", return_value=0) as clear_pyc, \
         patch("hermes_cli.main._install_python_dependencies_with_optional_fallback", return_value=None) as deps, \
         patch("hermes_cli.main._refresh_active_lazy_features", return_value=None) as lazy, \
         patch("hermes_cli.main._update_node_dependencies", return_value=None) as node, \
         patch("hermes_cli.main._build_web_ui", return_value=True) as web, \
         patch("hermes_cli.main._write_update_incomplete_marker", return_value=None) as mark, \
         patch("hermes_cli.main._clear_update_incomplete_marker", return_value=None) as unmark, \
         patch("hermes_cli.main._sync_canonical_soul_after_update", return_value=None), \
         patch("hermes_cli.main.subprocess.run", side_effect=_git_side_effect([])):
        assert _run_update() is None
    for mocked in (clear_pyc, deps, lazy, node, web, mark, unmark):
        assert mocked.call_count == 1, mocked
    out = capsys.readouterr().out
    assert "Code updated to v9.9.9" in out
    assert "Update complete" in out


def test_update_release_same_commit_is_a_noop_with_policy_repairs(root, capsys):
    _marker(root, tag="v9.9.9-rc.2", commit=COMMIT)
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9")), \
         patch("hermes_cli.release_update.apply_release_update") as apply_, \
         patch("hermes_cli.main._apply_aimds_defaults_after_update") as defaults, \
         patch("hermes_cli.main._seed_aimds_default_cron_after_update") as cron, \
         patch("hermes_cli.main._sync_canonical_soul_after_update") as soul, \
         patch("hermes_cli.main._run_post_update_pipeline") as pipeline:
        assert _run_update() is None
    apply_.assert_not_called()
    pipeline.assert_not_called()
    for hook in (defaults, cron, soul):
        hook.assert_called_once()
    assert "✓ Already up to date!" in capsys.readouterr().out
    assert read_release_marker(root)["tag"] == "v9.9.9-rc.2"  # untouched


def test_update_release_newer_local_is_never_downgraded(root, capsys):
    _marker(root, tag="v9.9.9-rc.1", commit="a" * 40)
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.8")), \
         patch("hermes_cli.release_update.apply_release_update") as apply_:
        assert _run_update() is None
    apply_.assert_not_called()
    assert "On v9.9.9-rc.1, newer than stable v9.9.8 — nothing to do." in capsys.readouterr().out


def test_update_release_apply_error_is_fatal_and_leaves_no_marker(root, capsys):
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9")), \
         patch("hermes_cli.release_update.apply_release_update", side_effect=ReleaseFeedError("sha256 mismatch")), \
         patch("hermes_cli.main._create_pre_update_snapshot", return_value=None), \
         patch("hermes_cli.main._run_post_update_pipeline") as pipeline, \
         patch("hermes_cli.main._update_via_legacy_archive") as legacy:
        assert _run_update() == 1
    pipeline.assert_not_called()
    legacy.assert_not_called()
    assert read_release_marker(root) is None
    out = capsys.readouterr().out
    assert "✗ Release update failed: sha256 mismatch" in out and "Your install is unchanged." in out


# ---------------------------------------------------------------------------
# fallback chain — no update deadlock
# ---------------------------------------------------------------------------

def test_update_manifest_error_auto_without_git_falls_back_to_legacy_archive(root, capsys):
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("HTTP 404")), \
         patch("hermes_cli.main._update_via_legacy_archive") as legacy, \
         patch("hermes_cli.main.subprocess.run") as run:
        assert _run_update() is None
    run.assert_not_called()
    legacy.assert_called_once()
    assert legacy.call_args.args[1] == "stable"
    out = capsys.readouterr().out
    assert "⚠ Release manifest unavailable: HTTP 404" in out
    assert "Falling back to the previous update path" in out


def _git_path_patches(calls):
    return [
        patch("hermes_cli.main._update_via_legacy_archive"),
        patch("hermes_cli.main._discard_lockfile_churn", return_value=None),
        patch("hermes_cli.main._get_origin_url", return_value="https://github.com/IAMDS-GMBH/AIMDS-Agent.git"),
        patch("hermes_cli.main._is_fork", return_value=False),
        patch("hermes_cli.main._stash_local_changes_if_needed", return_value=None),
        patch("hermes_cli.main._sync_canonical_soul_after_update", return_value=None),
        patch("hermes_cli.main.subprocess.run", side_effect=_git_side_effect(calls)),
    ]


def test_update_manifest_error_auto_with_git_continues_on_git_path(root, capsys):
    (root / ".git").mkdir()
    calls = []
    patches = _git_path_patches(calls)
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("HTTP 404")), \
         patches[0] as legacy, patches[1] as churn, patches[2], patches[3], patches[4], patches[5], patches[6]:
        assert _run_update(_args(branch="stable")) is None
    legacy.assert_not_called()
    churn.assert_called_once()
    assert any("fetch --force --tags origin" in j for j, _ in calls), [j for j, _ in calls]
    out = capsys.readouterr().out
    assert "⚠ Release manifest unavailable: HTTP 404" in out
    assert "Falling back to the previous update path" in out
    assert "Continuing with the git update path" in out
    assert "Already up to date" in out
    assert read_release_marker(root) is None


def test_update_auto_main_channel_with_git_skips_release_and_uses_git(root, capsys):
    """``main`` is git-only: in auto mode the release probe steps aside without an error."""
    (root / ".git").mkdir()
    calls = []
    patches = _git_path_patches(calls)
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", False)), \
         patch("hermes_cli.release_update.fetch_release_feed") as fetch, \
         patches[0] as legacy, patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        assert _run_update(_args(branch="main")) is None
    fetch.assert_not_called()
    legacy.assert_not_called()
    assert any(j.startswith("git fetch origin main") for j, _ in calls), [j for j, _ in calls]
    out = capsys.readouterr().out
    assert "'main' channel is git-only" in out
    assert "Already up to date" in out


def test_update_manifest_error_forced_release_has_no_fallback(root, capsys):
    (root / ".git").mkdir()
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", True)), \
         patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("HTTP 404")), \
         patch("hermes_cli.main._update_via_legacy_archive") as legacy, \
         patch("hermes_cli.main.subprocess.run") as run:
        assert _run_update() == 1
    legacy.assert_not_called()
    run.assert_not_called()
    out = capsys.readouterr().out
    assert "✗ Release manifest unavailable: HTTP 404" in out and "no fallback" in out


def test_update_forced_release_refuses_main_channel(root, capsys):
    with patch("hermes_cli.main._resolve_update_source", return_value=("release", True)), \
         patch("hermes_cli.release_update.fetch_release_feed") as fetch:
        assert _run_update(_args(branch="main")) == 1
    fetch.assert_not_called()
    assert "needs a git checkout" in capsys.readouterr().out


def test_update_existing_git_client_never_touches_the_release_repo(root, capsys):
    """A checkout whose origin answers stays on git — no manifest request at all."""
    (root / ".git").mkdir()
    calls = []
    with patch("hermes_cli.config.load_config", return_value=_config()), \
         patch("hermes_cli.release_update.fetch_release_feed") as fetch, \
         patch("hermes_cli.main._discard_lockfile_churn", return_value=None), \
         patch("hermes_cli.main._get_origin_url", return_value="https://github.com/IAMDS-GMBH/AIMDS-Agent.git"), \
         patch("hermes_cli.main._is_fork", return_value=False), \
         patch("hermes_cli.main._stash_local_changes_if_needed", return_value=None), \
         patch("hermes_cli.main._sync_canonical_soul_after_update", return_value=None), \
         patch("hermes_cli.main._run_pre_update_backup", return_value=None), \
         patch("hermes_cli.main.subprocess.run", side_effect=_git_side_effect(calls)):
        assert _run_update(_args(branch="main")) is None
    fetch.assert_not_called()
    assert any("ls-remote --exit-code origin HEAD" in j for j, _ in calls)
    assert "Already up to date" in capsys.readouterr().out


def test_legacy_archive_removes_marker_and_runs_pipeline(root, tmp_path, capsys):
    import hashlib
    import io
    import zipfile

    _marker(root, tag="v1.0.0", commit="a" * 40)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for marker in MARKER_FILES:
            zf.writestr(f"AIMDS-Agent-9.9.9/{marker}", "# from source repo\n")
    data = buffer.getvalue()

    def fake_urlretrieve(url, dest):
        assert url == "https://github.com/IAMDS-GMBH/AIMDS-Agent/archive/refs/tags/v9.9.9.zip"
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest, None

    with patch("hermes_cli.release_channels.latest_release_tag_via_api", return_value="v9.9.9"), \
         patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve), \
         patch("hermes_cli.main._create_pre_update_snapshot", return_value="snap-2"), \
         patch("hermes_cli.main._validate_critical_files_syntax", return_value=(True, None, None)), \
         patch("hermes_cli.main._run_post_update_pipeline") as pipeline:
        hermes_main._update_via_legacy_archive(_args(), "stable", gateway_mode=False, assume_yes=True)
    assert read_release_marker(root) is None
    assert (root / "pyproject.toml").read_text() == "# from source repo\n"
    pipeline.assert_called_once_with(gateway_mode=False, assume_yes=True, pre_update_snapshot_id="snap-2", code_updated_line="✓ Code updated to v9.9.9!")
    assert "Latest stable release: v9.9.9" in capsys.readouterr().out
    _ = hashlib  # keep import (mirrors release tests' helper set)


def test_legacy_archive_unresolvable_tag_exits_with_both_hints(root, capsys):
    with patch("hermes_cli.release_channels.latest_release_tag_via_api", return_value=None), \
         patch("urllib.request.urlretrieve") as fetch:
        with pytest.raises(SystemExit) as exc:
            hermes_main._update_via_legacy_archive(_args(), "stable", gateway_mode=False, assume_yes=True)
    assert exc.value.code == 1
    fetch.assert_not_called()
    out = capsys.readouterr().out
    assert RELEASE_REPO in out and "source repository" in out


# ---------------------------------------------------------------------------
# banner / dashboard
# ---------------------------------------------------------------------------

@pytest.fixture
def banner_env(root, tmp_path, monkeypatch):
    import hermes_cli.banner as banner

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_REVISION", raising=False)
    monkeypatch.setattr(banner, "_resolve_project_root", lambda: root)
    monkeypatch.setattr(banner, "VERSION", "1.0.0")
    return banner, home


def test_banner_release_check_newer(banner_env, root):
    banner, home = banner_env
    _marker(root, tag="v1.0.0", commit="a" * 40)
    with patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.config.load_config_readonly", return_value=_config(channel="preview")), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9", channel="preview")) as fetch, \
         patch("hermes_cli.banner.subprocess.run", side_effect=AssertionError("no git")), \
         patch("hermes_cli.banner._check_via_local_git") as local_git:
        assert banner.check_for_updates() == banner.UPDATE_AVAILABLE_NO_COUNT
    fetch.assert_called_once_with("preview")
    local_git.assert_not_called()
    cached = json.loads((home / ".update_check").read_text())
    assert (cached["channel"], cached["release_tag"], cached["release_version"], cached["release_build_id"]) == ("preview", "v9.9.9", "9.9.9", "2026-09-08T10:00:00+00:00")
    assert banner.get_release_update_info() == {"channel": "preview", "release_tag": "v9.9.9", "release_version": "9.9.9", "release_build_id": "2026-09-08T10:00:00+00:00"}


def test_banner_release_check_same_commit_and_cache(banner_env, root):
    banner, home = banner_env
    _marker(root, tag="v9.9.9", commit=COMMIT)
    with patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.config.load_config_readonly", return_value=_config()), \
         patch("hermes_cli.release_update.fetch_release_feed", return_value=_feed("9.9.9")) as fetch:
        assert banner.check_for_updates() == 0
        assert banner.check_for_updates() == 0  # cached for 24h
    assert fetch.call_count == 1


def test_banner_release_manifest_error_auto_falls_back_to_git_check(banner_env, root):
    banner, home = banner_env
    (root / ".git").mkdir()
    _marker(root)
    with patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.config.load_config_readonly", return_value=_config()), \
         patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("404")), \
         patch("hermes_cli.banner._check_via_local_git", return_value=3) as local_git:
        assert banner.check_for_updates() == 3
    local_git.assert_called_once_with(root)
    cached = json.loads((home / ".update_check").read_text())
    assert cached["release_tag"] is None and cached["channel"] == "stable"
    assert banner.get_release_update_info() is None


def test_banner_release_manifest_error_forced_reports_unknown(banner_env, root):
    banner, home = banner_env
    (root / ".git").mkdir()
    _marker(root)
    with patch("hermes_cli.config.detect_install_method", return_value="release"), \
         patch("hermes_cli.config.load_config_readonly", return_value=_config(source="release")), \
         patch("hermes_cli.release_update.fetch_release_feed", side_effect=ReleaseFeedError("404")), \
         patch("hermes_cli.banner._check_via_local_git") as local_git:
        assert banner.check_for_updates() is None
    local_git.assert_not_called()


def test_dashboard_update_check_exposes_release_fields(root):
    from hermes_cli import web_server

    info = {"channel": "stable", "release_tag": "v9.9.9", "release_version": "9.9.9", "release_build_id": "b"}
    with patch("hermes_cli.web_server.detect_install_method", return_value="release"), \
         patch("hermes_cli.banner.check_for_updates", return_value=-1), \
         patch("hermes_cli.banner.get_release_update_info", return_value=info):
        payload = asyncio.run(web_server.check_hermes_update())
    assert payload["install_method"] == "release" and payload["can_apply"] is True
    assert payload["update_available"] is True and payload["behind"] == -1
    assert (payload["channel"], payload["release_tag"], payload["release_version"], payload["release_build_id"]) == ("stable", "v9.9.9", "9.9.9", "b")
    assert payload["message"] == "Release v9.9.9 is available."
    assert payload["update_command"] == "hermes update"
    assert "commits" not in payload


def test_dashboard_update_check_git_payload_has_null_release_fields(root):
    from hermes_cli import web_server

    with patch("hermes_cli.web_server.detect_install_method", return_value="git"), \
         patch("hermes_cli.banner.check_for_updates", return_value=0):
        payload = asyncio.run(web_server.check_hermes_update())
    assert payload["release_tag"] is None and payload["channel"] is None
    assert payload["message"] == "You're on the latest version."


# ---------------------------------------------------------------------------
# config / version identity
# ---------------------------------------------------------------------------

def test_detect_install_method_marker_beats_git_stamp(root, tmp_path, monkeypatch):
    from hermes_cli import config as cfg

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / ".install_method").write_text("git\n")
    (root / ".git").mkdir()
    assert cfg.detect_install_method(root) == "git"
    _marker(root)
    assert cfg.detect_install_method(root) == "release"
    assert cfg.recommended_update_command_for_method("release") == "hermes update"


def test_release_marker_version_identity(root):
    import hermes_cli

    assert hermes_cli._get_release_marker_version(root) is None
    _marker(root, tag="v9.9.9")
    assert hermes_cli._get_release_marker_version(root) == "9.9.9"
