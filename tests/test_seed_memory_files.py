from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "installer"
    / "scripts"
    / "seed_memory_files.py"
)
_SPEC = importlib.util.spec_from_file_location("seed_memory_files", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

seed_memory_files = _MODULE.seed_memory_files


def test_seeds_missing_targets(tmp_path):
    seed_dir = tmp_path / "seeds"
    mem_dir = tmp_path / "memories"
    seed_dir.mkdir()
    mem_dir.mkdir()
    (seed_dir / "USER.seed.md").write_text("user-seed\n", encoding="utf-8")
    (seed_dir / "MEMORY.seed.md").write_text("memory-seed\n", encoding="utf-8")

    result = seed_memory_files(seed_dir, mem_dir)

    assert result == {"USER.md": "seeded", "MEMORY.md": "seeded"}
    assert (mem_dir / "USER.md").read_text(encoding="utf-8") == "user-seed\n"
    assert (mem_dir / "MEMORY.md").read_text(encoding="utf-8") == "memory-seed\n"


def test_keeps_existing_non_empty_targets(tmp_path):
    seed_dir = tmp_path / "seeds"
    mem_dir = tmp_path / "memories"
    seed_dir.mkdir()
    mem_dir.mkdir()
    (seed_dir / "USER.seed.md").write_text("new-user\n", encoding="utf-8")
    (seed_dir / "MEMORY.seed.md").write_text("new-memory\n", encoding="utf-8")
    (mem_dir / "USER.md").write_text("existing-user\n", encoding="utf-8")
    (mem_dir / "MEMORY.md").write_text("existing-memory\n", encoding="utf-8")

    result = seed_memory_files(seed_dir, mem_dir)

    assert result == {"USER.md": "kept-existing", "MEMORY.md": "kept-existing"}
    assert (mem_dir / "USER.md").read_text(encoding="utf-8") == "existing-user\n"
    assert (mem_dir / "MEMORY.md").read_text(encoding="utf-8") == "existing-memory\n"


def test_seeds_whitespace_only_targets(tmp_path):
    seed_dir = tmp_path / "seeds"
    mem_dir = tmp_path / "memories"
    seed_dir.mkdir()
    mem_dir.mkdir()
    (seed_dir / "USER.seed.md").write_text("user-seed\n", encoding="utf-8")
    (seed_dir / "MEMORY.seed.md").write_text("memory-seed\n", encoding="utf-8")
    (mem_dir / "USER.md").write_text(" \n\t", encoding="utf-8")
    (mem_dir / "MEMORY.md").write_text("\n", encoding="utf-8")

    result = seed_memory_files(seed_dir, mem_dir)

    assert result == {"USER.md": "seeded", "MEMORY.md": "seeded"}
