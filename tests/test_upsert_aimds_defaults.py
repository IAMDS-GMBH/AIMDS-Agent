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
_VERSION = _MODULE._AIMDS_DEFAULTS_VERSION

AIMDS_MAIN = {"provider": "aimds-suite-prod", "default": "AIMDS-Suite-Auto"}
KEY_SEES = ["AIMDS-Suite-Auto", "claude-haiku-4.5", "claude-sonnet-5", "vllm-custom"]


@pytest.fixture
def models(monkeypatch):
    """What the key can see on the provider; tests set ``seen[:]``."""
    seen = list(KEY_SEES)
    calls = []

    def _fake(provider):
        calls.append(provider)
        return [m.lower() for m in seen]

    monkeypatch.setattr(_MODULE, "_available_aimds_models", _fake)
    seen_box = type("Seen", (), {"list": seen, "calls": calls})()
    return seen_box


def _aux(out, slot):
    return out["auxiliary"][slot]


def test_upsert_aimds_defaults_creates_required_sections():
    cfg = {}
    out = upsert_aimds_defaults(cfg)

    assert out["tools"]["tool_search"]["enabled"] == "on"
    assert out["tools"]["tool_search"]["threshold_pct"] == 10
    assert out["prompt_caching"]["cache_ttl"] == "5m"
    assert out["memory"]["enforce_initial_memory_context"] is True
    assert out["memory"]["session_start_compact_workspace_hydration"] is True
    assert out["memory"]["session_start_bootstrap_contract_enabled"] is False

    for slot in ("goal_judge", "compression", "approval", "mcp", "title_generation"):
        assert slot not in out.get("auxiliary", {})  # no AIMDS main provider → not ours
    assert out["curator"]["prune_builtins"] is False

    include = out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["include"]
    assert "kb_search" in include
    assert "memory_context" in include
    assert out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["resources"] is False
    assert out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["prompts"] is False


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
    assert "kb_search" in out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["include"]
    assert out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["resources"] is False


def test_migrate_aimds_defaults_sets_version_and_applies_when_missing():
    cfg = {}
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is True
    assert f"applied v{_VERSION} (from v0)" in status
    assert out["aimds_defaults_version"] == _VERSION
    assert out["tools"]["tool_search"]["enabled"] == "on"


def test_migrate_aimds_defaults_reenforces_policy_when_already_current():
    cfg = {
        "aimds_defaults_version": _VERSION,
        "memory": {
            "enforce_initial_memory_context": False,
            "session_start_compact_workspace_hydration": False,
            "session_start_bootstrap_contract_enabled": True,
        },
    }
    out, changed, status = migrate_aimds_defaults(cfg)

    assert changed is True
    assert f"enforced policy v{_VERSION} (already current v{_VERSION})" in status
    enforced_line = next(l for l in status.splitlines() if l.startswith("enforced: "))
    assert "memory.enforce_initial_memory_context" in enforced_line
    assert "mcp_servers" in enforced_line
    assert out["memory"]["enforce_initial_memory_context"] is True
    assert out["memory"]["session_start_compact_workspace_hydration"] is True
    assert out["memory"]["session_start_bootstrap_contract_enabled"] is False


def test_upsert_removes_synthetic_iamds_stub_and_migrates_to_aimds_suite_mcp():
    cfg = {
        "mcp_servers": {
            "IAMDS": {
                "tools": {
                    "include": [
                        "kb_search",
                        "memory_context",
                    ],
                }
            },
        }
    }
    out = upsert_aimds_defaults(cfg)
    assert "IAMDS" not in out["mcp_servers"]
    assert "AIMDSSuiteMCP" in out["mcp_servers"]
    assert "kb_search" in out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["include"]


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
    assert "kb_search" in out["mcp_servers"]["AIMDSSuiteMCP"]["tools"]["include"]


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



# ---------------------------------------------------------------------------
# Auxiliary slots: fast model chosen from what the key can see
# ---------------------------------------------------------------------------


class TestAuxModelChoice:
    def test_v15_to_v16_points_gui_slots_and_goal_judge_at_the_fast_model(self, models):
        cfg = {"aimds_defaults_version": 15, "model": dict(AIMDS_MAIN)}
        out, _changed, status = migrate_aimds_defaults(cfg)
        for slot in ("compression", "title_generation", "approval", "mcp", "goal_judge"):
            assert _aux(out, slot) == {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}, slot
        assert out["aimds_defaults_version"] == _VERSION
        assert "auxiliary → aimds-suite-prod / claude-haiku-4.5 (provider lists 4 models)" in status
        assert models.calls == ["aimds-suite-prod"]  # one lookup per run

    def test_second_preference_when_the_key_does_not_see_haiku(self, models):
        models.list[:] = ["AIMDS-Suite-Auto", "gpt-5-mini", "gemini-3.6-flash"]
        out, _c, _s = migrate_aimds_defaults({"aimds_defaults_version": 15, "model": dict(AIMDS_MAIN)})
        assert _aux(out, "compression")["model"] == "gpt-5-mini"

    def test_main_model_when_no_preferred_model_is_visible(self, models):
        models.list[:] = ["AIMDS-Suite-Auto", "claude-sonnet-5"]
        out, _c, _s = migrate_aimds_defaults({"aimds_defaults_version": 15, "model": dict(AIMDS_MAIN)})
        assert _aux(out, "compression") == {"provider": "aimds-suite-prod", "model": "AIMDS-Suite-Auto"}

    def test_empty_model_list_fills_with_main_model_but_keeps_a_managed_slot(self, models):
        models.list[:] = []
        cfg = {
            "aimds_defaults_version": 15,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"goal_judge": {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}},
        }
        out, _c, status = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression") == {"provider": "aimds-suite-prod", "model": "AIMDS-Suite-Auto"}
        assert _aux(out, "goal_judge")["model"] == "claude-haiku-4.5"  # offline: not rewritten
        assert "(fallback: main model)" in status

    def test_missing_models_module_falls_back_without_crashing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _no_models(name, *a, **k):
            if name == "hermes_cli.models":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_models)
        out, _c, _s = migrate_aimds_defaults({"aimds_defaults_version": 15, "model": dict(AIMDS_MAIN)})
        assert _aux(out, "compression")["model"] == "AIMDS-Suite-Auto"

    def test_non_aimds_main_provider_leaves_slots_alone(self, models):
        cfg = {"aimds_defaults_version": 15, "model": {"provider": "openrouter", "default": "x"}}
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert "compression" not in out.get("auxiliary", {})
        assert "goal_judge" not in out.get("auxiliary", {})
        assert models.calls == []

    def test_placeholder_slot_is_cleaned_then_filled(self, models):
        cfg = {
            "aimds_defaults_version": 15,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"compression": {"provider": "openai_compatible", "base_url": "https://<litellm-host>/v1", "model": "<litellm-fast-model>"}},
        }
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression") == {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}

    def test_existing_slot_keys_survive(self, models):
        cfg = {
            "aimds_defaults_version": 15,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"compression": {"provider": "auto", "model": "", "timeout": 42}},
        }
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression") == {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5", "timeout": 42}


class TestOneShotSemantics:
    """GUI-owned slots are set once; afterwards the user's GUI choice stands."""

    def test_gui_choice_survives_the_v16_step(self, models):
        cfg = {
            "aimds_defaults_version": 15,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {
                "compression": {"provider": "openrouter", "model": "x"},
                "title_generation": {"provider": "aimds-suite-prod", "model": "claude-sonnet-5"},
            },
        }
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression") == {"provider": "openrouter", "model": "x"}
        assert _aux(out, "title_generation") == {"provider": "aimds-suite-prod", "model": "claude-sonnet-5"}
        assert _aux(out, "approval")["model"] == "claude-haiku-4.5"  # untouched slot still gets the default

    def test_reset_to_main_in_the_gui_is_not_refilled_when_already_current(self, models):
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"compression": {"provider": "auto", "model": ""}},
        }
        out, _c, status = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression") == {"provider": "auto", "model": ""}
        assert "auxiliary → aimds-suite-prod / claude-haiku-4.5" in status  # goal_judge (policy) still lands
        assert _aux(out, "goal_judge")["model"] == "claude-haiku-4.5"

    def test_goal_judge_is_policy_and_follows_a_better_visible_model(self, models):
        models.list[:] = ["AIMDS-Suite-Auto", "claude-haiku-4.5", "gpt-5-mini"]
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {
                "goal_judge": {"provider": "aimds-suite-prod", "model": "gpt-5-mini"},
                "compression": {"provider": "aimds-suite-prod", "model": "gpt-5-mini"},
            },
        }
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "goal_judge")["model"] == "claude-haiku-4.5"
        assert _aux(out, "compression")["model"] == "gpt-5-mini"  # GUI slot: still visible → untouched

    def test_goal_judge_set_by_cli_to_something_else_is_enforced_back(self, models):
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"goal_judge": {"provider": "openrouter", "model": "x"}},
        }
        out, _c, status = migrate_aimds_defaults(cfg)
        assert _aux(out, "goal_judge") == {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}
        enforced_line = next(l for l in status.splitlines() if l.startswith("enforced: "))
        assert "auxiliary.goal_judge" in enforced_line


class TestHealing:
    def test_vanished_model_is_replaced_on_a_managed_gui_slot(self, models):
        models.list[:] = ["AIMDS-Suite-Auto", "gpt-5-mini"]
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"compression": {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}},
        }
        out, _c, status = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression")["model"] == "gpt-5-mini"
        assert "auxiliary → aimds-suite-prod / gpt-5-mini" in status

    def test_provider_follows_main_provider_from_prod_to_staging(self, models):
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": {"provider": "aimds-suite-staging", "default": "AIMDS-Suite-Auto"},
            "auxiliary": {
                "compression": {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"},
                "approval": {"provider": "openrouter", "model": "x"},
            },
        }
        out, _c, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression")["provider"] == "aimds-suite-staging"
        assert _aux(out, "approval") == {"provider": "openrouter", "model": "x"}

    def test_vanished_model_is_kept_when_the_list_is_empty(self, models):
        models.list[:] = []
        cfg = {
            "aimds_defaults_version": _VERSION,
            "model": dict(AIMDS_MAIN),
            "auxiliary": {"compression": {"provider": "aimds-suite-prod", "model": "claude-haiku-4.5"}},
        }
        out, changed, _s = migrate_aimds_defaults(cfg)
        assert _aux(out, "compression")["model"] == "claude-haiku-4.5"


class TestAdvancedReset:
    def test_deviating_advanced_value_is_reset_once_with_a_status_line(self, models):
        cfg = {"aimds_defaults_version": 15, "agent": {"max_turns": 60}, "terminal": {"timeout": 180}}
        out, _c, status = migrate_aimds_defaults(cfg)
        from hermes_cli.config import DEFAULT_CONFIG

        assert out["agent"]["max_turns"] == DEFAULT_CONFIG["agent"]["max_turns"]
        assert f"advanced reset: agent.max_turns 60 → {DEFAULT_CONFIG['agent']['max_turns']!r}" in status
        assert "terminal.timeout" not in status  # already standard → no line
        assert "delegation" not in out  # missing paths are not materialised

    def test_no_reset_once_current(self, models):
        cfg = {"aimds_defaults_version": _VERSION, "agent": {"max_turns": 60}}
        out, _c, status = migrate_aimds_defaults(cfg)
        assert out["agent"]["max_turns"] == 60
        assert "advanced reset" not in status

    def test_missing_default_config_defers_the_step_and_does_not_stamp(self, models, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _no_config(name, *a, **k):
            if name == "hermes_cli.config":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_config)
        cfg = {"aimds_defaults_version": 15, "agent": {"max_turns": 60}, "model": dict(AIMDS_MAIN)}
        out, _c, status = migrate_aimds_defaults(cfg)
        assert out["aimds_defaults_version"] == 15
        assert "one-shot deferred" in status
        assert out["agent"]["max_turns"] == 60
        assert _aux(out, "goal_judge")["model"] == "claude-haiku-4.5"  # policy still applied


def test_enforced_policy_names_every_path_the_policy_writes(models):
    """Every leaf ``upsert_aimds_defaults`` writes must be covered by
    ``_AIMDS_ENFORCED_POLICY`` (prefix match), so the update log is honest."""
    out = upsert_aimds_defaults({"model": dict(AIMDS_MAIN)})
    out.pop("model")

    def leaves(node, prefix=""):
        if isinstance(node, dict) and node:
            for k, v in node.items():
                yield from leaves(v, f"{prefix}{k}.")
        else:
            yield prefix.rstrip(".")

    policy = set(_MODULE._AIMDS_ENFORCED_POLICY)
    uncovered = [
        leaf for leaf in leaves(out)
        if not any(leaf == p or leaf.startswith(p + ".") for p in policy)
    ]
    assert uncovered == []
