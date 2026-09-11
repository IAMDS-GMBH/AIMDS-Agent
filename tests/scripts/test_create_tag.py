"""createTag.sh (AIS-292): candidates from main, promotion to stable, guards."""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "createTag.sh"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / "f.txt").write_text("1\n")
    _git(work, "add", "."); _git(work, "commit", "-q", "-m", "one")
    _git(work, "tag", "-a", "v0.7.4", "-m", "v0.7.4")
    _git(work, "push", "-q", "origin", "main", "--tags")
    return work


def _tag(work, *args):
    proc = subprocess.run(["bash", str(SCRIPT), *args, "--yes"], cwd=work, capture_output=True, text=True, env=GIT_ENV)
    return proc.returncode, proc.stdout + proc.stderr


def _commit(work, msg):
    (work / "f.txt").write_text(msg + "\n")
    _git(work, "commit", "-qam", msg)
    _git(work, "push", "-q", "origin", "main")


def test_patch_cuts_rc1_then_rc2_and_promote_stable(repo):
    code, out = _tag(repo, "patch")
    assert code == 0, out
    assert "v0.7.5-rc.1" in out
    assert _git(repo, "ls-remote", "--tags", "origin").count("v0.7.5-rc.1") >= 1
    # HEAD already tagged → nothing new
    code, out = _tag(repo, "patch")
    assert code == 0 and "nothing new" in out
    _commit(repo, "two")
    code, out = _tag(repo, "minor")  # open candidate line wins over the bump type
    assert code == 0 and "v0.7.5-rc.2" in out and "ignoring bump type" in out
    rc2_sha = _git(repo, "rev-list", "-n", "1", "v0.7.5-rc.2")
    _commit(repo, "three")  # main moves on; promotion still uses the candidate's commit
    code, out = _tag(repo, "promote", "stable")
    assert code == 0, out
    assert "v0.7.5" in out and "NOT part of this release" in out
    assert _git(repo, "rev-list", "-n", "1", "v0.7.5") == rc2_sha
    assert _git(repo, "cat-file", "-t", "v0.7.5") == "tag"  # annotated
    # nothing above stable → promote refuses, next patch opens 0.7.6
    code, out = _tag(repo, "promote", "stable")
    assert code != 0 and "not above the highest stable" in out
    code, out = _tag(repo, "patch")
    assert code == 0 and "v0.7.6-rc.1" in out


def test_guards_branch_sync_and_dry_run(repo):
    _git(repo, "checkout", "-qb", "feature/x")
    code, out = _tag(repo, "patch")
    assert code != 0 and "'main' only" in out
    _git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("local\n"); _git(repo, "commit", "-qam", "unpushed")
    code, out = _tag(repo, "patch")
    assert code != 0 and "ahead of origin/main" in out
    _git(repo, "reset", "-q", "--hard", "origin/main")
    code, out = _tag(repo, "patch", "--dry-run")
    assert code == 0 and "(dry-run) would run" in out and "v0.7.5-rc.1" in out
    assert "v0.7.5-rc.1" not in _git(repo, "tag", "-l")
    code, out = _tag(repo, "status")
    assert code == 0 and "highest stable:    v0.7.4" in out and "HEAD tag:" in out
    code, out = _tag(repo, "promote", "stable")
    assert code != 0 and "No release candidate" in out
