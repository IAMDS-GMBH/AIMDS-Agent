from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "installer"
    / "scripts"
    / "upsert_aimds_defaults.py"
)
_SPEC = importlib.util.spec_from_file_location("upsert_aimds_defaults", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

upsert_aimds_defaults = _MODULE.upsert_aimds_defaults
migrate_aimds_defaults = _MODULE.migrate_aimds_defaults


def test_upsert_aimds_defaults_creates_required_sections():
    cfg = {}
    out = upsert_aimds_defaults(cfg)

    assert out["tools"]["tool_search"]["enabled"] == "on"
    assert out["tools"]["tool_search"]["threshold_pct"] == 10
    assert out["prompt_caching"]["cache_ttl"] == "5m"
    assert out["memory"]["enforce_initial_memory_context"] is False
    assert out["memory"]["session_start_compact_workspace_hydration"] is False
    assert out["memory"]["session_start_bootstrap_contract_enabled"] is False

    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        assert slot not in out.get("auxiliary", {})

    include = out["mcp_servers"]["IAMDS"]["tools"]["include"]
    assert "kb_search" in include
    assert "memory_context" in include
    assert out["mcp_servers"]["IAMDS"]["tools"]["resources"] is False
    assert out["mcp_servers"]["IAMDS"]["tools"]["prompts"] is False


def test_upsert_aimds_defaults_overrides_existing_conflicting_values():
    cfg = {
        "tools": {"tool_search": {"enabled": "off", "threshold_pct": 50}},
        "prompt_caching": {"cache_ttl": "1h"},
        "auxiliary": {"goal_judge": {"provider": "openai_compatible", "base_url": "https://<litellm-host>/v1", "model": "<litellm-fast-model>"}},
        "mcp_servers": {"IAMDS": {"tools": {"include": ["legacy"], "resources": True}}},
    }

    out = upsert_aimds_defaults(cfg)
    assert out["tools"]["tool_search"]["enabled"] == "on"
    assert out["tools"]["tool_search"]["threshold_pct"] == 10
    assert out["prompt_caching"]["cache_ttl"] == "5m"
    assert "goal_judge" not in out["auxiliary"]
    assert "kb_search" in out["mcp_servers"]["IAMDS"]["tools"]["include"]
    assert out["mcp_servers"]["IAMDS"]["tools"]["resources"] is False


def test_migrate_aimds_defaults_sets_version_and_applies_when_missing():
    cfg = {}
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is True
    assert "applied v14 (from v0)" in status
    assert out["aimds_defaults_version"] == 14
    assert out["tools"]["tool_search"]["enabled"] == "on"


def test_migrate_aimds_defaults_reenforces_policy_when_already_current():
    cfg = {
        "aimds_defaults_version": 14,
        "memory": {
            "enforce_initial_memory_context": True,
            "session_start_compact_workspace_hydration": True,
            "session_start_bootstrap_contract_enabled": True,
        },
    }
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is True
    assert "enforced policy v14 (already current v14)" in status
    assert out["memory"]["enforce_initial_memory_context"] is False
    assert out["memory"]["session_start_compact_workspace_hydration"] is False
    assert out["memory"]["session_start_bootstrap_contract_enabled"] is False


def test_upsert_removes_synthetic_aimds_gateway_when_iamds_exists():
    cfg = {
        "mcp_servers": {
            "IAMDS": {
                "provider": "iamds",
                "url": "https://example/mcp",
            },
            "aimds-gateway": {
                "tools": {
                    "include": [
                        "kb_search",
                        "kb_get_topic",
                        "kb_get_related",
                        "memory_context",
                        "memory_get",
                        "memory_list",
                        "memory_upsert",
                        "memory_delete",
                    ],
                    "resources": False,
                    "prompts": False,
                }
            },
        }
    }
    out = upsert_aimds_defaults(cfg)
    assert "aimds-gateway" not in out["mcp_servers"]
    assert "kb_search" in out["mcp_servers"]["IAMDS"]["tools"]["include"]


def test_upsert_targets_provider_iamds_even_with_custom_server_name():
    cfg = {
        "mcp_servers": {
            "custom-tools": {
                "provider": "something-else",
                "url": "https://example/tools",
            },
            "corp-gateway": {
                "provider": "iamds",
                "url": "https://example/iamds-mcp",
                "tools": {"include": ["legacy"]},
            },
        }
    }

    out = upsert_aimds_defaults(cfg)
    include = out["mcp_servers"]["corp-gateway"]["tools"]["include"]
    assert "kb_search" in include
    assert "memory_context" in include
    assert out["mcp_servers"]["custom-tools"].get("tools") is None


class TestMainUsesAtomicWrite:
    """Root-cause regression coverage: main() must write config.yaml via
    the same atomic temp-file + fsync + os.replace primitive as
    hermes_cli/config.py::save_config(), not a bare path.write_text()
    (which could leave the file partially written / racing with another
    writer -- see incident: two mapping entries spliced onto one line).
    """

    def test_main_calls_atomic_yaml_write(self, tmp_path, monkeypatch):
        import yaml as _yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(_yaml.safe_dump({"model": {"default": "x"}}), encoding="utf-8")

        calls = []
        original_atomic_yaml_write = _MODULE.atomic_yaml_write

        def _spy(path, data, **kwargs):
            calls.append((path, data))
            return original_atomic_yaml_write(path, data, **kwargs)

        monkeypatch.setattr(_MODULE, "atomic_yaml_write", _spy)
        monkeypatch.setattr(sys, "argv", ["upsert_aimds_defaults.py", str(config_path)])

        rc = _MODULE.main(["upsert_aimds_defaults.py", str(config_path)])

        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == config_path
        # Confirm the write actually landed and is parseable.
        result = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert result["aimds_defaults_version"] == _MODULE._AIMDS_DEFAULTS_VERSION

    def test_main_does_not_use_bare_write_text(self, tmp_path, monkeypatch):
        import yaml as _yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(_yaml.safe_dump({"model": {"default": "x"}}), encoding="utf-8")

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("Path.write_text must not be used for config.yaml writes")

        monkeypatch.setattr(Path, "write_text", _fail_if_called)

        rc = _MODULE.main(["upsert_aimds_defaults.py", str(config_path)])

        assert rc == 0
        result = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert result["aimds_defaults_version"] == _MODULE._AIMDS_DEFAULTS_VERSION

    def test_write_survives_simulated_mid_write_crash(self, tmp_path, monkeypatch):
        """A crash mid-dump must leave the previous valid file intact
        (never a partially-written/corrupted config.yaml)."""
        import yaml as _yaml

        config_path = tmp_path / "config.yaml"
        original_text = _yaml.safe_dump({"model": {"default": "untouched"}})
        config_path.write_text(original_text, encoding="utf-8")

        class SimulatedCrash(BaseException):
            pass

        monkeypatch.setattr(_MODULE.yaml, "dump", lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash()))

        with pytest.raises(SimulatedCrash):
            _MODULE.main(["upsert_aimds_defaults.py", str(config_path)])

        # No leftover temp files, and the original file is untouched.
        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
        assert tmp_files == []
        assert config_path.read_text(encoding="utf-8") == original_text

