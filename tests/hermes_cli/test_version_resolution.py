"""Version identity of a source checkout (AIS-318): highest reachable release tag + commits."""

import subprocess

import pytest

import hermes_cli


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")

    def commit(msg):
        (root / "f.txt").write_text(msg)
        _git(root, "add", "f.txt")
        _git(root, "commit", "-q", "-m", msg)

    commit("one")
    _git(root, "tag", "v0.7.4")
    commit("two")
    _git(root, "tag", "-a", "v0.7.5-rc.1", "-m", "rc")
    return root, commit


def test_on_tag_reports_the_tag_only(repo):
    root, _ = repo
    assert hermes_cli._get_release_describe_version(root) == "0.7.5-rc.1"


def test_past_tag_counts_commits_from_highest_reachable_release_tag(repo):
    root, commit = repo
    commit("three")
    commit("four")
    assert hermes_cli._get_release_describe_version(root) == "0.7.5-rc.1+2"
    # promote: stable on the rc commit outranks the candidate, the count stays
    _git(root, "tag", "v0.7.5", "v0.7.5-rc.1")
    assert hermes_cli._get_release_describe_version(root) == "0.7.5+2"


def test_non_release_tags_and_unreachable_tags_are_ignored(repo):
    root, commit = repo
    commit("three")
    _git(root, "tag", "nightly-1")
    _git(root, "checkout", "-q", "-b", "side", "v0.7.4")
    commit("side-work")
    # v0.7.5-rc.1 is not reachable from the side branch
    assert hermes_cli._get_release_describe_version(root) == "0.7.4+1"


def test_without_git_or_tags_returns_none(tmp_path, repo):
    assert hermes_cli._get_release_describe_version(tmp_path) is None
    root = tmp_path / "bare"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "f").write_text("x")
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "x")
    assert hermes_cli._get_release_describe_version(root) is None


def test_version_tuple_ignores_local_suffix():
    from hermes_cli.banner import _version_tuple

    assert _version_tuple("0.7.5+6") == (0, 7, 5)
    assert _version_tuple("0.7.5") == (0, 7, 5)
