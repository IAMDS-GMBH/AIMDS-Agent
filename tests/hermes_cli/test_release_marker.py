"""Tests for the release marker (.hermes-release.json, AIS-312)."""

import json

from hermes_cli import release_marker
from hermes_cli.release_marker import (
    MARKER_FILENAME,
    MARKER_FORMAT,
    is_release_managed,
    read_release_marker,
    remove_release_marker,
    write_release_marker,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _write(tmp_path, **kw):
    fields = dict(channel="stable", tag="v1.2.3", version="1.2.3", commit_sha=COMMIT, sha256="a" * 64, build_id="b1")
    fields.update(kw)
    return write_release_marker(tmp_path, **fields)


def test_roundtrip(tmp_path):
    path = _write(tmp_path)
    assert path == tmp_path / MARKER_FILENAME
    marker = read_release_marker(tmp_path)
    assert marker["format"] == MARKER_FORMAT
    assert (marker["channel"], marker["tag"], marker["version"], marker["commit_sha"]) == ("stable", "v1.2.3", "1.2.3", COMMIT)
    assert marker["sha256"] == "a" * 64 and marker["build_id"] == "b1"
    assert marker["applied_at"].endswith("+00:00")
    assert is_release_managed(tmp_path)


def test_write_is_atomic_and_overwrites(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _write(root, tag="v1.2.3", version="1.2.3", applied_at="2026-01-01T00:00:00+00:00")
    _write(root, tag="v1.2.4", version="1.2.4")
    assert read_release_marker(root)["tag"] == "v1.2.4"
    assert not (root / (MARKER_FILENAME + ".tmp")).exists()
    assert sorted(p.name for p in root.iterdir()) == [MARKER_FILENAME]


def test_missing_or_malformed_marker_reads_as_none(tmp_path):
    assert read_release_marker(tmp_path) is None
    assert not is_release_managed(tmp_path)
    path = tmp_path / MARKER_FILENAME
    for content in ("not json{", "[]", json.dumps({"format": "other-v1", "tag": "v1"}),
                    json.dumps({"format": MARKER_FORMAT, "channel": "stable", "tag": "v1.2.3", "version": "1.2.3"}),
                    json.dumps({"format": MARKER_FORMAT, "channel": "stable", "tag": "", "version": "1.2.3", "commit_sha": COMMIT})):
        path.write_text(content)
        assert read_release_marker(tmp_path) is None, content
    path.write_bytes(b"\xff\xfe")
    assert read_release_marker(tmp_path) is None


def test_remove_release_marker(tmp_path):
    assert remove_release_marker(tmp_path) is False
    _write(tmp_path)
    assert remove_release_marker(tmp_path) is True
    assert not (tmp_path / MARKER_FILENAME).exists()


def test_marker_path(tmp_path):
    assert release_marker.marker_path(tmp_path) == tmp_path / MARKER_FILENAME
