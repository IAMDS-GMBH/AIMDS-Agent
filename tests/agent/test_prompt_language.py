"""Everything the model reads ships in English; it answers in the user's language.

German prompt text costs more tokens for the same instruction and the model
mirrors the user's language anyway. This guards the surfaces that are loaded
every turn (SOUL, calendar block, data-handling ladder, core tool schemas)
and the shipped skill bodies (loaded on demand, ~30 kB when they were German).
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Function words that do not occur in English prose; proper nouns (Bayern,
# Arbeitszeit-Profil, Feiertag names) never match these.
_GERMAN = re.compile(
    r"\b(und|nicht|immer|niemals|bitte|nutze|prüfe|sollte|müssen|werden|keine|oder|für|mit|wenn|dann|"
    r"wird|sind|auch|nur|alle|diese|einen|einer|beim|über|zur|zum|nach|vor)\b",
    re.IGNORECASE,
)
# Quoted example utterances ("problem melden") are user-facing triggers and may stay.
_QUOTED = re.compile(r"[\"'„»]([^\"'“»]{1,80})[\"'“«]")


def _german_hits(text: str):
    stripped = _QUOTED.sub(" ", text)
    return sorted({m.group(0).lower() for m in _GERMAN.finditer(stripped)})


def test_soul_files_are_english_and_do_not_force_german():
    for rel in ("installer/skills-hidden/aimds-loadout/identity/SOUL.md", "docker/SOUL.md"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not _german_hits(text), (rel, _german_hits(text))
        assert "Default to German" not in text
        assert "user's language" in text
    from hermes_cli.default_soul import DEFAULT_SOUL_MD

    assert not _german_hits(DEFAULT_SOUL_MD)


def test_calendar_block_is_english():
    import hermes_time

    ctx = hermes_time.get_calendar_context(datetime(2026, 7, 30, 12, tzinfo=timezone.utc), state="BY")
    block = ctx["formatted_prompt"]
    assert not _german_hits(block), _german_hits(block)
    assert "Thursday" in block and "Monday" in block


def test_data_handling_ladder_and_core_tool_schemas_are_english():
    from agent.prompt_builder import build_data_handling_guidance
    import toolsets
    from tools.registry import registry

    ladder = build_data_handling_guidance({"sql", "workdays", "terminal", "tool_search", "read_file"})
    assert not _german_hits(ladder), _german_hits(ladder)

    core = set(toolsets._HERMES_CORE_TOOLS)
    offenders = {}
    for entry in registry._snapshot_entries():
        if entry.name not in core:
            continue
        schema = entry.schema if isinstance(entry.schema, dict) else {}
        text = str(schema.get("description", "")) + " " + str(schema.get("parameters", ""))
        hits = _german_hits(text)
        if hits:
            offenders[entry.name] = hits
    assert not offenders, offenders


@pytest.mark.parametrize("skill_md", sorted((_REPO_ROOT / "skills" / "aimds_custom").glob("*/SKILL.md")), ids=lambda p: p.parent.name)
def test_shipped_skill_bodies_are_english(skill_md: Path):
    text = skill_md.read_text(encoding="utf-8")
    # code blocks carry SQL / paths / German column names — not prose
    prose = re.sub(r"```.*?```", " ", text, flags=re.S)
    assert not _german_hits(prose), _german_hits(prose)


def test_memory_seeds_and_cron_prompts_are_english():
    for rel in ("installer/skills-hidden/aimds-loadout/memory/USER.seed.md", "installer/skills-hidden/aimds-loadout/memory/MEMORY.seed.md"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not _german_hits(text), (rel, _german_hits(text))
    seeds = (_REPO_ROOT / "installer" / "scripts" / "seed_default_cron_jobs.py").read_text(encoding="utf-8")
    assert "German or English" not in seeds
    assert not _german_hits(seeds), _german_hits(seeds)


def test_loadout_pack_ships_no_skill_duplicates():
    assert not (_REPO_ROOT / "installer" / "skills-hidden" / "aimds-loadout" / "skills").exists()
