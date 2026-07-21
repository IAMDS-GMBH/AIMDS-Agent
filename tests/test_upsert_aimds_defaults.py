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

    assert out["mcp_servers"]["aimds-gateway"]["tools"]["include"] == [
        "kb_search",
        "kb_get_topic",
        "kb_get_related",
        "memory_get",
        "memory_list",
        "memory_upsert",
        "memory_delete",
    ]
    assert out["mcp_servers"]["aimds-gateway"]["tools"]["resources"] is False
    assert out["mcp_servers"]["aimds-gateway"]["tools"]["prompts"] is False


def test_upsert_aimds_defaults_overrides_existing_conflicting_values():
    cfg = {
        "tools": {"tool_search": {"enabled": "off", "threshold_pct": 50}},
        "prompt_caching": {"cache_ttl": "1h"},
        "auxiliary": {"goal_judge": {"provider": "openrouter", "model": "foo"}},
        "mcp_servers": {"aimds-gateway": {"tools": {"include": ["legacy"], "resources": True}}},
    }

    out = upsert_aimds_defaults(cfg)
    assert out["tools"]["tool_search"]["enabled"] == "on"
    assert out["tools"]["tool_search"]["threshold_pct"] == 10
    assert out["prompt_caching"]["cache_ttl"] == "5m"
    assert out["auxiliary"]["goal_judge"]["provider"] == "openai_compatible"
    assert out["mcp_servers"]["aimds-gateway"]["tools"]["include"][0] == "kb_search"
    assert out["mcp_servers"]["aimds-gateway"]["tools"]["resources"] is False
