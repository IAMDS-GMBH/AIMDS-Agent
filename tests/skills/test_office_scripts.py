from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_word_convert_run_pandoc_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module(
        REPO_ROOT / "skills/productivity/word/scripts/convert.py",
        "word_convert_script",
    )

    def _raise_file_not_found(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("pandoc not found")

    monkeypatch.setattr(mod.subprocess, "run", _raise_file_not_found)
    ok, err = mod._run_pandoc(Path("in.docx"), Path("out.md"))
    assert ok is False
    assert err == "pandoc not found"


def test_excel_write_resolves_soffice_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module(
        REPO_ROOT / "skills/productivity/excel/scripts/write.py",
        "excel_write_script",
    )

    def _which(name: str) -> str | None:
        return "/usr/bin/soffice" if name == "soffice" else None

    monkeypatch.setattr(mod.shutil, "which", _which)
    assert mod._libreoffice_cmd() == "/usr/bin/soffice"


def test_excel_read_csv_single_cell_exits() -> None:
    mod = _load_module(
        REPO_ROOT / "skills/productivity/excel/scripts/read.py",
        "excel_read_script",
    )

    assert mod._is_csv("report.csv") is True
    assert mod._is_csv("report.xlsx") is False
    with pytest.raises(SystemExit):
        mod.read_cell("report.csv", "A1")
