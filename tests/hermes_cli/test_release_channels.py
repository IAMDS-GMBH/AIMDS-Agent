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
    assert rc.is_tag_channel("tags") and rc.is_tag_channel("preview") and not rc.is_tag_channel("main")


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
