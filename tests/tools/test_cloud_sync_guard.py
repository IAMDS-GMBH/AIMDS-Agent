"""OneDrive-safe content search (AIS-495, SUP-20261005-102357).

Placeholder attributes only exist on Windows, so the "online-only" predicate
is injected; the rg runs below are real (local terminal backend).
"""

import json
import shutil

import pytest

from tools import cloud_sync_guard
from tools.cloud_sync_guard import plan_content_search, sync_roots, write_ignore_file
from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations, _regex_error_hint

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


def _online_only(names):
    return lambda entry: entry.name in names


@pytest.fixture
def home(tmp_path):
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "notes.txt").write_text("DevOps local\n")
    cloud = tmp_path / "OneDrive - IAMDS"
    (cloud / "Team").mkdir(parents=True)
    (cloud / "Team" / "local.txt").write_text("DevOps synced and downloaded\n")
    (cloud / "Team" / "remote.txt").write_text("DevOps online only\n")
    (cloud / "Team" / "[draft] *notes*.txt").write_text("DevOps online only, odd name\n")
    return tmp_path


def test_sync_roots_from_env_and_home(home, tmp_path):
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    roots = sync_roots({"OneDriveCommercial": str(extra), "OneDrive": str(home / "missing")}, home=str(home))
    assert str(extra) in roots
    assert str(home / "OneDrive - IAMDS") in roots
    assert all("missing" not in r for r in roots)


def test_search_above_root_skips_root(home):
    plan = plan_content_search(str(home), [str(home / "OneDrive - IAMDS")])
    assert plan.skipped_roots == ["OneDrive - IAMDS"]
    assert not plan.cloud_only_files and plan.refuse is None
    assert "OneDrive - IAMDS" in plan.note()


def test_unrelated_path_is_untouched(home):
    plan = plan_content_search(str(home / "Projects"), [str(home / "OneDrive - IAMDS")])
    assert not plan.active


def test_search_inside_root_collects_online_only_files(home):
    root = home / "OneDrive - IAMDS"
    plan = plan_content_search(
        str(root / "Team"), [str(root)],
        cloud_only=_online_only({"remote.txt", "[draft] *notes*.txt"}),
    )
    assert sorted(plan.cloud_only_files) == ["[draft] *notes*.txt", "remote.txt"]
    assert "2 online-only" in plan.note()


def test_huge_cloud_tree_is_refused(home):
    root = home / "OneDrive - IAMDS"
    plan = plan_content_search(str(root), [str(root)], cloud_only=lambda e: False, walk_limit=2)
    assert plan.refuse and "subfolder" in plan.refuse


@needs_rg
def test_ignore_file_keeps_rg_off_online_only_files(home, monkeypatch):
    root = home / "OneDrive - IAMDS"
    monkeypatch.setattr(ShellFileOperations, "_cloud_guard_enabled", lambda self: True)
    monkeypatch.setattr(cloud_sync_guard, "sync_roots", lambda: [str(root)])
    monkeypatch.setattr(
        cloud_sync_guard, "is_cloud_only",
        _online_only({"remote.txt", "[draft] *notes*.txt"}),
    )
    ops = ShellFileOperations(LocalEnvironment(cwd=str(home)), cwd=str(home))

    result = ops.search("DevOps", path=str(root), output_mode="files_only").to_dict()
    assert [f.rsplit("/", 1)[-1] for f in result["files"]] == ["local.txt"]
    assert "2 online-only" in result["note"]
    assert not list(home.glob("hermes-cloud-only-*"))

    result = ops.search("DevOps", path=str(home), output_mode="files_only").to_dict()
    assert [f.rsplit("/", 1)[-1] for f in result["files"]] == ["notes.txt"]
    assert "skipped cloud-synced folder" in result["note"]


@needs_rg
def test_guard_off_searches_everything(home):
    ops = ShellFileOperations(LocalEnvironment(cwd=str(home)), cwd=str(home))
    result = ops.search("DevOps", path=str(home), output_mode="files_only").to_dict()
    assert len(result["files"]) == 4 and "note" not in result


def test_write_ignore_file_escapes_gitignore_syntax(tmp_path):
    path = write_ignore_file(["a/[draft] *notes*.txt", "#hash.txt", "!bang.txt"])
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines == ["**/a/\\[draft\\] \\*notes\\*.txt", "**/\\#hash.txt", "**/\\!bang.txt"]


@needs_rg
def test_glob_as_regex_gets_a_hint(tmp_path):
    (tmp_path / "a.txt").write_text("DevOps\n")
    ops = ShellFileOperations(LocalEnvironment(cwd=str(tmp_path)), cwd=str(tmp_path))
    result = ops.search("(?:*DevOps*)", path=str(tmp_path)).to_dict()
    assert "regex parse error" in result["error"]
    assert "not a glob" in result["error"]
    assert json.dumps(result)


def test_hint_only_for_regex_errors():
    assert _regex_error_hint("rg: ./x: Permission denied") == ""
    assert "not a glob" in _regex_error_hint("grep: Invalid preceding regular expression")
