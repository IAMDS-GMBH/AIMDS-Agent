"""hermes_cli/release_channels.py (AIS-292): channel names, release tags, selection."""

import io
import json
from unittest.mock import patch

from hermes_cli import release_channels as rc


def test_normalize_channel_aliases_and_branches():
    assert rc.normalize_channel(None) == "main"
    assert rc.normalize_channel("  ") == "main"
    assert rc.normalize_channel("tags") == "stable"
    assert rc.normalize_channel("Stable") == "stable"
    assert rc.normalize_channel("preview") == "preview"
    assert rc.normalize_channel("bb/gui") == "bb/gui"
    assert rc.normalize_channel("Auto") == "auto"  # config sentinel, not a branch (AIS-299)
    assert rc.is_tag_channel("tags") and rc.is_tag_channel("preview") and not rc.is_tag_channel("main")
    assert not rc.is_tag_channel("auto")


def test_parse_and_sort_release_tags():
    assert rc.parse_release_tag("v0.7.5") == (0, 7, 5, None)
    assert rc.parse_release_tag("v0.7.5-rc.3") == (0, 7, 5, 3)
    assert rc.parse_release_tag("0.7.5") is None and rc.parse_release_tag("v0.7") is None
    assert rc.parse_release_tag("v0.7.5-beta.1") is None
    ordered = sorted(["v0.7.5-rc.2", "v0.7.5", "v0.7.5-rc.10", "v0.7.4", "v0.8.0-rc.1"], key=rc.release_sort_key)
    assert ordered == ["v0.7.4", "v0.7.5-rc.2", "v0.7.5-rc.10", "v0.7.5", "v0.8.0-rc.1"]


def test_select_release_tag_per_channel():
    tags = ["v0.7.4", "v0.7.5-rc.1", "v0.7.5-rc.2", "junk", "nightly-1", "v0.7.5-beta.1"]
    assert rc.select_release_tag(tags, "stable") == "v0.7.4"
    assert rc.select_release_tag(tags, "tags") == "v0.7.4"
    assert rc.select_release_tag(tags, "preview") == "v0.7.5-rc.2"
    assert rc.select_release_tag(tags + ["v0.7.5"], "preview") == "v0.7.5"  # stable beats its own rcs
    assert rc.select_release_tag(tags + ["v0.7.5"], "stable") == "v0.7.5"
    assert rc.select_release_tag(tags, "main") is None
    assert rc.select_release_tag(["junk"], "stable") is None
    assert rc.select_release_tag([], "preview") is None


def test_head_release_tag_and_newer_comparison():
    assert rc.compare_release_tags("v0.7.5-rc.1", "v0.7.4") > 0
    assert rc.compare_release_tags("v0.7.5-rc.1", "v0.7.5") < 0
    assert rc.compare_release_tags("v0.7.5", "v0.7.5") == 0
    assert rc.head_release_tag(["nightly-1", "v0.7.5-rc.1"]) == "v0.7.5-rc.1"
    assert rc.head_release_tag(["v0.7.5-rc.2", "v0.7.5"]) == "v0.7.5"  # promoted commit carries both
    assert rc.head_release_tag(["nightly-1"]) is None and rc.head_release_tag([]) is None
    assert rc.release_tag_is_newer("v0.7.5-rc.1", "v0.7.4") is True
    assert rc.release_tag_is_newer("v0.7.5-rc.1", "v0.7.5-rc.2") is False
    assert rc.release_tag_is_newer("v0.7.5", "v0.7.5") is False
    assert rc.release_tag_is_newer(None, "v0.7.4") is False
    assert rc.release_tag_is_newer("junk", "v0.7.4") is False


def test_resolve_head_vs_target_states():
    # SUP-20260907 (AIS-299): HEAD on v0.7.5-rc.1, stable channel targets v0.7.4 — no downgrade.
    assert rc.resolve_head_vs_target(head_sha="rc1", target_sha="stable4", head_tags=["v0.7.5-rc.1"], target_tag="v0.7.4") == "newer"
    # preview channel: rc.2 exists → a real update, decided by rev-list.
    assert rc.resolve_head_vs_target(head_sha="rc1", target_sha="rc2", head_tags=["v0.7.5-rc.1"], target_tag="v0.7.5-rc.2") == "other"
    # promoted on the same commit → sha equality wins for both channels.
    assert rc.resolve_head_vs_target(head_sha="same", target_sha="same", head_tags=["v0.7.5-rc.2", "v0.7.5"], target_tag="v0.7.5") == "at-target"
    # untagged main checkout past the release → caller keeps the AIS-297 rules.
    assert rc.resolve_head_vs_target(head_sha="dev", target_sha="stable4", head_tags=[], target_tag="v0.7.4") == "other"
    assert rc.resolve_head_vs_target(head_sha="dev", target_sha="stable4", head_tags=["nightly-1"], target_tag="v0.7.4") == "other"
    assert rc.resolve_head_vs_target(head_sha="", target_sha="", head_tags=[], target_tag="v0.7.4") == "other"


def test_version_helpers_and_archive_url():
    assert rc.version_from_tag("v0.7.5-rc.1") == "0.7.5-rc.1"
    assert rc.stable_version_of("v0.7.5-rc.1") == "0.7.5" and rc.stable_version_of("x") is None
    assert rc.github_archive_url("main") == "https://github.com/IAMDS-GMBH/AIMDS-Agent/archive/refs/heads/main.zip"
    assert rc.github_archive_url("v0.7.5", kind="tags") == "https://github.com/IAMDS-GMBH/AIMDS-Agent/archive/refs/tags/v0.7.5.zip"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_latest_release_tag_via_api():
    def fake_urlopen(request, timeout=0):
        if request.full_url.endswith("/releases/latest"):
            return _Resp(json.dumps({"tag_name": "v0.7.5"}).encode())
        return _Resp(json.dumps([
            {"tag_name": "v0.7.6-rc.1", "draft": False},
            {"tag_name": "v0.7.6-rc.2", "draft": True},
            {"tag_name": "v0.7.5", "draft": False},
        ]).encode())

    with patch.object(rc.urllib.request, "urlopen", side_effect=fake_urlopen):
        assert rc.latest_release_tag_via_api("stable") == "v0.7.5"
        assert rc.latest_release_tag_via_api("preview") == "v0.7.6-rc.1"  # drafts ignored
        assert rc.latest_release_tag_via_api("main") is None
    with patch.object(rc.urllib.request, "urlopen", side_effect=OSError("offline")):
        assert rc.latest_release_tag_via_api("stable") is None


def test_tag_fits_channel_and_update_source():
    assert rc.tag_fits_channel("v0.7.5", "stable") and rc.tag_fits_channel("v0.7.5", "preview")
    assert rc.tag_fits_channel("v0.7.5-rc.1", "preview") and not rc.tag_fits_channel("v0.7.5-rc.1", "stable")
    assert rc.tag_fits_channel("v0.7.5", "tags")  # alias
    assert not rc.tag_fits_channel("v0.7.5", "main") and not rc.tag_fits_channel("junk", "preview")
    assert rc.normalize_update_source(None) == "auto" and rc.normalize_update_source(" Release ") == "release"
    assert rc.normalize_update_source("git") == "git" and rc.normalize_update_source("stable") == "auto"
    # the *channel* alias "release" must never leak into the source vocabulary and vice versa
    assert rc.normalize_channel("release") == "stable"


def test_release_repo_urls():
    assert rc.RELEASE_REPO == "IAMDS-GMBH/AIMDS-Agent-Releases"
    assert rc.release_download_url("v0.7.6", "hermes-source-0.7.6.zip") == "https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/download/v0.7.6/hermes-source-0.7.6.zip"
    assert rc.latest_manifest_url() == "https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/latest/download/hermes-release.json"
    assert rc.github_archive_url("v0.7.5", kind="tags").startswith("https://github.com/IAMDS-GMBH/AIMDS-Agent/")  # legacy stays on the source repo


def test_fetch_release_via_api(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout=0):
        seen.append((request.full_url, dict(request.header_items())))
        if request.full_url.endswith("/releases/latest"):
            return _Resp(json.dumps({"tag_name": "v0.7.5", "assets": [{"name": "hermes-release.json"}]}).encode())
        return _Resp(json.dumps([
            {"tag_name": "v0.7.6-rc.1", "draft": False, "assets": []},
            {"tag_name": "v0.7.6-rc.2", "draft": True, "assets": []},
            {"tag_name": "v0.7.5", "draft": False, "assets": []},
            {"tag_name": "nightly-3", "draft": False, "assets": []},
        ]).encode())

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with patch.object(rc.urllib.request, "urlopen", side_effect=fake_urlopen):
        stable = rc.fetch_release_via_api("stable")
        assert stable["tag_name"] == "v0.7.5" and stable["assets"][0]["name"] == "hermes-release.json"
        assert seen[-1][0] == "https://api.github.com/repos/IAMDS-GMBH/AIMDS-Agent-Releases/releases/latest"
        assert "Authorization" not in seen[-1][1]
        preview = rc.fetch_release_via_api("preview")
        assert preview["tag_name"] == "v0.7.6-rc.1"  # drafts and non-release tags ignored
        assert rc.fetch_release_via_api("main") is None
        assert rc.fetch_release_via_api("stable", repo="IAMDS-GMBH/AIMDS-Agent")["tag_name"] == "v0.7.5"
        assert seen[-1][0].startswith("https://api.github.com/repos/IAMDS-GMBH/AIMDS-Agent/")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    with patch.object(rc.urllib.request, "urlopen", side_effect=fake_urlopen):
        rc.fetch_release_via_api("stable")
    assert seen[-1][1]["Authorization"] == "Bearer tok"
    with patch.object(rc.urllib.request, "urlopen", side_effect=OSError("offline")):
        assert rc.fetch_release_via_api("stable") is None

    # /releases/latest answering with a candidate is not a stable release
    with patch.object(rc.urllib.request, "urlopen", side_effect=lambda r, timeout=0: _Resp(json.dumps({"tag_name": "v0.7.6-rc.1"}).encode())):
        assert rc.fetch_release_via_api("stable") is None
