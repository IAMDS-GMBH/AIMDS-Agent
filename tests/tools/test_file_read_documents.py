"""read_file on Office/PDF files returns Markdown (AIS-294).

Run with:  python -m pytest tests/tools/test_file_read_documents.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import document_convert as dc
from tools.file_tools import read_file_tool, reset_file_dedup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_DOCUMENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HERMES_DOCUMENT_CONVERTER", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    reset_file_dedup("default")
    yield
    reset_file_dedup("default")


def _docx(path: Path, lines) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for text in lines:
        document.add_paragraph(text)
    document.save(str(path))
    return path


def test_docx_is_returned_as_markdown(tmp_path):
    src = _docx(tmp_path / "umzugsplan.docx", [f"Zeile {i}" for i in range(1, 11)])
    out = json.loads(read_file_tool(str(src)))
    assert "error" not in out, out
    assert out["converted_from"] == str(src.resolve())
    assert out["converter"].startswith("local-")
    assert Path(out["cache_path"]).suffix == ".md"
    assert "Zeile 1" in out["content"] and "Zeile 10" in out["content"]
    assert out["total_lines"] >= 10


def test_offset_and_limit_apply_to_converted_text(tmp_path):
    src = _docx(tmp_path / "long.docx", [f"Absatz {i}" for i in range(1, 41)])
    full = json.loads(read_file_tool(str(src)))
    window = json.loads(read_file_tool(str(src), offset=3, limit=2))
    assert "error" not in window
    assert window["content"].count("\n") <= 2
    assert full["total_lines"] == window["total_lines"]


def test_pdf_never_returns_raw_bytes(tmp_path):
    pytest.importorskip("pypdf")
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Rechnung 4711")
    pdf.output(str(tmp_path / "r.pdf"))
    out = json.loads(read_file_tool(str(tmp_path / "r.pdf")))
    assert "error" not in out, out
    assert "Rechnung 4711" in out["content"]
    assert "%PDF" not in out["content"]


def test_legacy_doc_gets_explicit_message(tmp_path):
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0 legacy")
    out = json.loads(read_file_tool(str(tmp_path / "old.doc")))
    assert "legacy binary Office format" in out["error"]


def test_other_binaries_keep_the_guard(tmp_path):
    (tmp_path / "x.zip").write_bytes(b"PK\x03\x04")
    out = json.loads(read_file_tool(str(tmp_path / "x.zip")))
    assert "Cannot read binary file" in out["error"]


def test_conversion_failure_is_an_error_with_hint(tmp_path, monkeypatch):
    src = _docx(tmp_path / "a.docx", ["x"])
    monkeypatch.setattr(dc, "_has_module", lambda name: False)
    out = json.loads(read_file_tool(str(src)))
    assert "office extra" in out["error"]
    assert "Do not parse the file with terminal commands" in out["error"]
    assert "office_word" in out["hint"]


def test_remote_sandbox_refuses_conversion(tmp_path, monkeypatch):
    src = _docx(tmp_path / "a.docx", ["x"])
    import tools.file_tools as ft

    real = ft._get_file_ops

    def remote_ops(task_id="default"):
        ops = real(task_id)
        monkeypatch.setattr(type(ops), "_lsp_local_only", lambda self: False, raising=False)
        return ops

    monkeypatch.setattr(ft, "_get_file_ops", remote_ops)
    out = json.loads(read_file_tool(str(src)))
    assert "remote sandbox" in out["error"]


def test_second_read_hits_dedup_not_reconversion(tmp_path, monkeypatch):
    src = _docx(tmp_path / "a.docx", ["once"])
    first = json.loads(read_file_tool(str(src)))
    assert "once" in first["content"]
    calls = []
    real = dc.convert_document
    monkeypatch.setattr(dc, "convert_document", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    second = json.loads(read_file_tool(str(src)))
    assert second.get("dedup") is True and calls == []
