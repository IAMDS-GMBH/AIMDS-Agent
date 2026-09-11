"""The msal pin must be identical everywhere it is declared (AIS-286).

Three places install msal: the dev extra (CI runs the M365 tests), the lazy
dependency table (runtime), and the MSOffice365MCP venv requirements.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pin(text: str) -> set[str]:
    return set(re.findall(r"msal==([0-9][0-9A-Za-z.]*)", text))


def test_msal_pin_is_consistent():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lazy = (ROOT / "tools" / "lazy_deps.py").read_text(encoding="utf-8")
    req = (ROOT / "optional-mcps" / "MSOffice365MCP" / "requirements.txt").read_text(encoding="utf-8")

    dev_line = next(line for line in pyproject.splitlines() if line.startswith("dev = ["))
    assert _pin(dev_line), "msal must be pinned in the dev extra so CI can import the M365 server"
    assert _pin(dev_line) == _pin(lazy) == _pin(req), (_pin(dev_line), _pin(lazy), _pin(req))
