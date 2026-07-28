"""Tests for the config.yaml "glued mapping keys" auto-repair sanitizer.

Covers the corruption pattern described in the incident: two YAML mapping
entries spliced onto one physical line (e.g. ``trusted: false  IAMDS:``)
with an indentation jump on the following line. Before this repair,
``load_config()`` / ``read_raw_config()`` would silently discard every
user override and fall back to ``DEFAULT_CONFIG`` whenever this happened.
"""

import os
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_cli.config import (
    _repair_corrupt_config_yaml,
    _sanitize_config_yaml_lines,
    load_config,
    read_raw_config,
)


_SPLICE_RAW = (
    "a:\n"
    "  b:\n"
    "    c:\n"
    "      d:\n"
    "        e:\n"
    "          f:\n"
    "            g:\n"
    "                trusted: false  IAMDS:\n"
    "    provider: iamds\n"
    "    url: https://example.com\n"
)


class TestSanitizeConfigYamlLines:
    def test_detects_and_splits_glued_keys(self):
        lines = _SPLICE_RAW.splitlines(keepends=True)
        new_lines, changed = _sanitize_config_yaml_lines(lines)

        assert changed is True
        joined = "".join(new_lines)
        # The two glued entries are now on separate physical lines.
        assert "trusted: false  IAMDS:" not in joined
        assert "trusted: false\n" in joined
        assert "    IAMDS:\n" in joined

    def test_leaves_clean_yaml_untouched(self):
        raw = "a:\n  b: 1\n  c: 2\n"
        lines = raw.splitlines(keepends=True)
        new_lines, changed = _sanitize_config_yaml_lines(lines)

        assert changed is False
        assert new_lines == lines

    def test_does_not_touch_comments_or_blank_lines(self):
        raw = "# a comment  with:extra  colons:\na:\n  b: 1\n\n"
        lines = raw.splitlines(keepends=True)
        new_lines, changed = _sanitize_config_yaml_lines(lines)

        assert changed is False
        assert new_lines == lines

    def test_glued_lines_without_scalar_value_are_left_alone(self):
        # "foo:  bar:" has no value between the two keys -- more likely a
        # nested-mapping typo than the glued-line corruption we target.
        raw = "foo:  bar:\n  baz: 1\n"
        lines = raw.splitlines(keepends=True)
        new_lines, changed = _sanitize_config_yaml_lines(lines)

        assert changed is False
        assert new_lines == lines


class TestRepairCorruptConfigYaml:
    def test_original_raw_fails_to_parse(self):
        try:
            yaml.safe_load(_SPLICE_RAW)
            assert False, "expected the raw splice corruption to fail parsing"
        except yaml.YAMLError:
            pass

    def test_repairs_the_exact_splice_pattern(self):
        repaired = _repair_corrupt_config_yaml(Path("/nonexistent/config.yaml"), _SPLICE_RAW)

        assert repaired is not None
        assert isinstance(repaired, dict)
        # The originally-glued "IAMDS" key and its neighboring settings
        # were recovered rather than discarded.
        inner = repaired["a"]["b"]["c"]["d"]["e"]["f"]["g"]
        assert inner["trusted"] is False
        assert repaired["a"]["b"]["IAMDS"] is None
        assert repaired["a"]["b"]["provider"] == "iamds"
        assert repaired["a"]["b"]["url"] == "https://example.com"

    def test_returns_none_for_unrepairable_yaml(self):
        # A YAML error unrelated to the glued-key pattern (unclosed flow
        # sequence) shouldn't be papered over -- there's no glued-key shape
        # here for the sanitizer to act on, so it must give up cleanly.
        raw = "a: [1, 2\n"
        repaired = _repair_corrupt_config_yaml(Path("/nonexistent/config.yaml"), raw)
        assert repaired is None


class TestLoadConfigRepairIntegration:
    def test_load_config_recovers_overrides_instead_of_defaults(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            (
                "model:\n"
                "  default: my-custom-model\n"
                "mcp_servers:\n"
                "  IAMDS:\n"
                "    headers:\n"
                "      Authorization: xxx\n"
                "    trusted: false  Extra:\n"
                "    provider: iamds\n"
                "    url: https://example.com\n"
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            cfg = load_config()

        # The user's model override survived instead of being replaced by
        # DEFAULT_CONFIG's default model.
        assert cfg["model"]["default"] == "my-custom-model"
        assert cfg["mcp_servers"]["IAMDS"]["provider"] == "iamds"

    def test_read_raw_config_recovers_overrides_instead_of_empty_dict(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            (
                "model:\n"
                "  default: my-custom-model  Extra:\n"
                "    provider: iamds\n"
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            raw = read_raw_config()

        assert raw != {}
        assert raw["model"]["default"] == "my-custom-model"
