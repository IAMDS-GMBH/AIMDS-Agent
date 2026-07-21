from __future__ import annotations

import importlib.util
from pathlib import Path


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

    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        assert out["auxiliary"][slot]["provider"] == "openai_compatible"
        assert out["auxiliary"][slot]["base_url"] == "https://<litellm-host>/v1"
        assert out["auxiliary"][slot]["model"] == "<litellm-fast-model>"

    assert out["mcp_servers"]["IAMDS"]["tools"]["include"] == [
        "kb_search",
        "kb_get_topic",
        "kb_get_related",
        "memory_get",
        "memory_list",
        "memory_upsert",
        "memory_delete",
    ]
    assert out["mcp_servers"]["IAMDS"]["tools"]["resources"] is False
    assert out["mcp_servers"]["IAMDS"]["tools"]["prompts"] is False


def test_upsert_aimds_defaults_overrides_existing_conflicting_values():
    cfg = {
        "tools": {"tool_search": {"enabled": "off", "threshold_pct": 50}},
        "prompt_caching": {"cache_ttl": "1h"},
        "auxiliary": {"goal_judge": {"provider": "openrouter", "model": "foo"}},
        "mcp_servers": {"IAMDS": {"tools": {"include": ["legacy"], "resources": True}}},
    }

    out = upsert_aimds_defaults(cfg)
    assert out["tools"]["tool_search"]["enabled"] == "on"
    assert out["tools"]["tool_search"]["threshold_pct"] == 10
    assert out["prompt_caching"]["cache_ttl"] == "5m"
    assert out["auxiliary"]["goal_judge"]["provider"] == "openai_compatible"
    assert out["mcp_servers"]["IAMDS"]["tools"]["include"][0] == "kb_search"
    assert out["mcp_servers"]["IAMDS"]["tools"]["resources"] is False


def test_migrate_aimds_defaults_sets_version_and_applies_when_missing():
    cfg = {}
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is True
    assert "applied v3 (from v0)" in status
    assert out["aimds_defaults_version"] == 3
    assert out["tools"]["tool_search"]["enabled"] == "on"


def test_migrate_aimds_defaults_skips_when_already_current():
    cfg = {
        "aimds_defaults_version": 3,
        "tools": {"tool_search": {"enabled": "off"}},
    }
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is False
    assert "already current (v3)" in status
    assert out["tools"]["tool_search"]["enabled"] == "off"


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
    assert out["mcp_servers"]["IAMDS"]["tools"]["include"][0] == "kb_search"


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
    assert out["mcp_servers"]["corp-gateway"]["tools"]["include"][0] == "kb_search"
    assert out["mcp_servers"]["custom-tools"].get("tools") is None
