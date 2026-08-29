"""The installed client runs on Python 3.11 (requires-python >=3.11); the dev
venv is 3.12. PEP 701 lets 3.12 put backslashes inside f-string expressions —
3.11 raises SyntaxError at import time, which took the `workdays` tool down
with "f-string expression part cannot include a backslash (memory_facade.py,
line 318)". ast.parse(feature_version=(3, 11)) does not reject it, so this
uses the 3.12 tokenizer: a backslash in any token inside an f-string's {…}
expression part is exactly what 3.11 refuses.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
_SOURCE_DIRS = ("agent", "tools", "hermes_cli", "gateway", "installer", "acp_adapter", "cron", "plugins", "utils")

# Cheap pre-filter: a brace group on one line that contains a backslash. Only
# files matching it are parsed and walked.
_SUSPECT = re.compile(r"\{[^{}\n]*\\[^{}\n]*\}")


def _py_files():
    yield from sorted(_REPO_ROOT.glob("*.py"))
    for name in _SOURCE_DIRS:
        base = _REPO_ROOT / name
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))


def _pep701_offences(source: str) -> list:
    """Backslashes inside the {…} expression parts of f-strings (3.12 tokenizer view)."""
    if not _SUSPECT.search(source):
        return []
    import io
    import tokenize

    offences = []
    fstring_depth = 0
    brace_depth = []  # one counter per open f-string
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.FSTRING_START:
                fstring_depth += 1
                brace_depth.append(0)
                continue
            if tok.type == tokenize.FSTRING_END:
                fstring_depth -= 1
                brace_depth.pop()
                continue
            if not fstring_depth:
                continue
            if tok.type == tokenize.OP and tok.string == "{":
                brace_depth[-1] += 1
            elif tok.type == tokenize.OP and tok.string == "}":
                brace_depth[-1] = max(0, brace_depth[-1] - 1)
            elif brace_depth[-1] > 0 and tok.type != tokenize.FSTRING_MIDDLE and "\\" in tok.string:
                offences.append((tok.start[0], tok.string[:80]))
    except (tokenize.TokenError, SyntaxError):
        return []
    return offences


def test_detector_catches_the_real_case():
    assert _pep701_offences('x = f"{json.dumps(t) if re.search(r\'[:#\\\\s]\', t) else t}"\n')
    assert not _pep701_offences('x = f"a {b} \\n"\n')  # backslash in the literal part is fine


@pytest.mark.parametrize("path", sorted(_py_files()), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_no_python_312_only_fstring_syntax(path: Path):
    source = path.read_text(encoding="utf-8", errors="replace")
    assert not _pep701_offences(source), _pep701_offences(source)
