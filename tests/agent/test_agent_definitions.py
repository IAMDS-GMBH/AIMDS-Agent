"""Lean subagent definitions (AIS-456)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import agent_definitions as ad


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


DIGEST = """---
name: digest
description: Read inputs in parts.
surfaces: [tui, cli]
toolsets: [file, mcp]
model: fast
max_iterations: 12
max_result_chars: 900
writes: false
---
You are a digest agent.
"""


def test_parse_reads_every_field():
    d = ad.parse_definition(DIGEST, source="x/digest.md")
    assert d.name == "digest" and d.description == "Read inputs in parts."
    assert d.toolsets == ("file", "mcp") and d.wants_mcp and d.plain_toolsets == ["file"]
    assert d.model == "fast" and d.max_iterations == 12 and d.max_result_chars == 900
    assert d.writes is False and d.body == "You are a digest agent." and len(d.digest) == 12


@pytest.mark.parametrize("text", [
    "no frontmatter at all",
    "---\nname: x\ndescription: d\n---\nbody",          # name too short
    "---\nname: digest\n---\nbody",                    # no description
    "---\nname: digest\ndescription: d\n---\n",        # no body
])
def test_unusable_files_are_skipped(text):
    assert ad.parse_definition(text) is None


def test_unknown_model_tier_and_surfaces_fall_back():
    d = ad.parse_definition("---\nname: draft\ndescription: d\nmodel: turbo\nsurfaces: [web]\n---\nb")
    assert d.model == "inherit" and d.surfaces == ("tui", "cli")


def test_home_override_wins_and_surfaces_filter(tmp_path):
    home, bundle = tmp_path / "home", tmp_path / "bundle"
    _write(bundle, "digest", DIGEST)
    _write(bundle, "explore", "---\nname: explore\ndescription: Find code.\nsurfaces: [cli]\n---\nExplore.")
    _write(home, "digest", DIGEST.replace("You are a digest agent.", "My own digest."))
    dirs = [home, bundle]
    assert ad.get_definition("digest", dirs=dirs).body == "My own digest."
    assert [d.name for d in ad.definitions_for("tui", dirs=dirs)] == ["digest"]
    assert [d.name for d in ad.definitions_for("cli", dirs=dirs)] == ["digest", "explore"]
    assert ad.get_definition("explore", platform="tui", dirs=dirs) is None
    assert ad.get_definition("explore", platform="cli", dirs=dirs).name == "explore"


def test_bundled_definitions_are_lean_english_and_read_only():
    bundled = ad.load_definitions([ad.BUNDLED_AGENTS_DIR])
    assert set(bundled) == {
        "digest", "collect", "sweep", "research", "draft",
        "explore", "verify", "review", "investigate",
    }
    for d in bundled.values():
        assert not d.writes, d.name
        assert len(d.body) < 1500, d.name
        assert d.body.isascii() or "—" in d.body, d.name  # English prose
    for name in ("explore", "verify", "review", "investigate"):
        assert bundled[name].surfaces == ("cli",)


# --------------------------------------------------------------------------- lean prompt


def _lean_agent(**overrides):
    values = dict(
        _lean_system_prompt=True, _lean_identity="You are a digest agent.",
        valid_tool_names={"read_file", "search_files"}, pass_session_id=False,
        session_id="s", model="AIMDS-Suite-Auto", provider="aimds-suite-prod",
        platform="tui", skip_context_files=True, load_soul_identity=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_lean_prompt_is_small_and_carries_the_definition(monkeypatch):
    from agent import system_prompt

    parts = system_prompt.build_system_prompt_parts(_lean_agent())
    joined = "\n\n".join(parts[k] for k in ("stable", "context", "volatile") if parts[k])
    assert joined.startswith("You are a digest agent.")
    assert len(joined) < 6000
    for heavy in ("available_skills", "Active Hermes profile", "Memory vault"):
        assert heavy not in joined
    assert "Model: AIMDS-Suite-Auto" in parts["volatile"]


def test_lean_prompt_without_definition_uses_the_generic_identity():
    from agent import system_prompt

    parts = system_prompt.build_system_prompt_parts(_lean_agent(_lean_identity="", valid_tool_names=set()))
    assert parts["stable"].startswith(system_prompt.LEAN_SUBAGENT_IDENTITY)
