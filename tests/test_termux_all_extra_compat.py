"""Regression coverage for the Termux broad install profile."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def test_pyproject_defines_termux_all_without_known_blockers() -> None:
    text = PYPROJECT.read_text()
    assert "termux-all = [" in text
    import re

    name = re.search(r'^name\s*=\s*"([^"]+)"', text, re.M).group(1)
    termux_all = text.split("termux-all = [", 1)[1].split("]", 1)[0]
    assert f'"{name}[termux]"' in text
    assert f'"{name}[matrix]"' not in termux_all
    assert f'"{name}[voice]"' not in termux_all


def test_install_script_prefers_termux_all_then_fallbacks() -> None:
    text = INSTALL_SH.read_text()
    assert "pip install -e '.[termux-all]' -c constraints-termux.txt" in text
    assert "Termux broad profile (.[termux-all]) failed, trying baseline Termux profile..." in text
    assert "Termux baseline profile (.[termux]) failed, trying base install..." in text
