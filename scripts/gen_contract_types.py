#!/usr/bin/env python3
"""Regenerate apps/shared/src/contracts.ts from agent/contracts/*.schema.json (AIS-797).

Usage:
  python scripts/gen_contract_types.py          # write the file
  python scripts/gen_contract_types.py --check  # exit 1 when it is stale
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.contracts.typescript import TARGET, render  # noqa: E402


def main(argv: list) -> int:
    target = REPO_ROOT / TARGET
    text = render()
    if "--check" in argv:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print(f"{TARGET} is stale — run: python scripts/gen_contract_types.py", file=sys.stderr)
            return 1
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
