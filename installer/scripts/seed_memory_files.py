#!/usr/bin/env python3
"""Seed local Hermes memory files from AIMDS seed templates.

Usage:
    python seed_memory_files.py <seed_dir> <memories_dir>

Rules:
1. Copy USER.seed.md -> USER.md and MEMORY.seed.md -> MEMORY.md
2. Seed only when target is missing or effectively empty (whitespace-only)
3. Never overwrite non-empty target files
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def _is_effectively_empty(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return path.read_text(encoding="utf-8").strip() == ""
    except OSError:
        return False


def _seed_one(source: Path, target: Path) -> str:
    if not source.is_file():
        return "missing-seed"
    if not _is_effectively_empty(target):
        return "kept-existing"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return "seeded"


def seed_memory_files(seed_dir: Path, memories_dir: Path) -> dict[str, str]:
    return {
        "USER.md": _seed_one(seed_dir / "USER.seed.md", memories_dir / "USER.md"),
        "MEMORY.md": _seed_one(seed_dir / "MEMORY.seed.md", memories_dir / "MEMORY.md"),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"Usage: {argv[0]} <seed_dir> <memories_dir>", file=sys.stderr)
        return 1

    seed_dir = Path(argv[1]).expanduser()
    memories_dir = Path(argv[2]).expanduser()
    result = seed_memory_files(seed_dir, memories_dir)
    for name, status in result.items():
        print(f"{name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
