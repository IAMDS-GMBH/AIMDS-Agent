"""CSV/TSV files are read as a paged Markdown table (AIS-600).

SUP-20261013-161622: a cp1252 ``;`` export came back from ``read_file`` with
replacement characters, so the agent ran pandas in the terminal and the
approval prompt timed out. These tests pin encoding + delimiter detection,
paging and the bounded output, and that ``read_file`` takes this path.

Run with:  python -m pytest tests/tools/test_table_reader.py -v
"""

from __future__ import annotations

import codecs
import json
from pathlib import Path

import pytest

from tools import table_reader as tr
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


WINDOWS_EXPORT = (
    "Name;Straße;Betrag;Bemerkung\r\n"
    "Müller;Hauptstraße 1;1.234,50 €;Überweisung\r\n"
    "Schäfer;Gößweinsteiner Weg 3;99,00 €;bar\r\n"
    ";;;\r\n"
)


def _cp1252_csv(path: Path) -> Path:
    path.write_bytes(WINDOWS_EXPORT.encode("cp1252"))
    return path


class TestEncoding:
    def test_bom_wins(self):
        assert tr.detect_encoding(codecs.BOM_UTF8 + "a,ü".encode()) == "utf-8-sig"

    def test_strict_utf8(self):
        assert tr.detect_encoding("Müller;€".encode()) == "utf-8"

    def test_windows_export_is_cp1252(self):
        assert tr.detect_encoding(WINDOWS_EXPORT.encode("cp1252")) == "cp1252"

    def test_latin1_is_last_resort(self):
        # 0x81 is undefined in cp1252, so only latin-1 decodes it.
        assert tr.detect_encoding(b"a;b\x81") == "latin-1"


class TestDelimiter:
    def test_semicolon_with_decimal_commas(self):
        sample = "Name;Betrag\nA;1,50\nB;2,00\nC;3,25\n"
        assert tr.detect_delimiter(sample) == ";"

    def test_comma(self):
        assert tr.detect_delimiter('id,name,note\n1,"a; b",x\n2,c,y\n') == ","

    def test_tab(self):
        assert tr.detect_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"

    def test_single_column_falls_back(self):
        assert tr.detect_delimiter("only\none\ncolumn\n") == ","


class TestReadTable:
    def test_cp1252_semicolon_export(self, tmp_path):
        page = tr.read_table(str(_cp1252_csv(tmp_path / "abrechnung.csv")))
        assert page.encoding == "cp1252" and page.delimiter == ";"
        assert page.columns == ["Name", "Straße", "Betrag", "Bemerkung"]
        assert page.total_rows == 2  # the blank ";;;" row is dropped
        assert "| 1 | Müller | Hauptstraße 1 | 1.234,50 € | Überweisung |" in page.content
        assert "Gößweinsteiner" in page.content
        assert "�" not in page.content
        assert not page.truncated and page.hint is None

    def test_utf8_bom_comma(self, tmp_path):
        src = tmp_path / "bom.csv"
        src.write_bytes(codecs.BOM_UTF8 + "Stadt,Einwohner\nMünchen,1500000\nKöln,1000000\n".encode())
        page = tr.read_table(str(src))
        assert page.encoding == "utf-8-sig" and page.delimiter == ","
        assert page.columns[0] == "Stadt"  # no phantom U+FEFF
        assert "| 1 | München | 1500000 |" in page.content

    def test_tsv(self, tmp_path):
        src = tmp_path / "data.tsv"
        src.write_text("a\tb\n1\t2, 3\n", encoding="utf-8")
        out = tr.read_table(str(src)).to_dict()
        assert out["delimiter"] == "tab" and out["columns"] == ["a", "b"]
        assert "| 1 | 1 | 2, 3 |" in out["content"]

    def test_excel_sep_preamble(self, tmp_path):
        src = tmp_path / "x.csv"
        src.write_text("sep=,\na;b,c\n1;2,3\n", encoding="utf-8")
        page = tr.read_table(str(src))
        assert page.delimiter == "," and page.columns == ["a;b", "c"]

    def test_cells_are_escaped_and_clipped(self, tmp_path):
        src = tmp_path / "x.csv"
        src.write_text('a,b\n"x|y","line1\nline2"\n' + "z," + "w" * 500 + "\n", encoding="utf-8")
        page = tr.read_table(str(src))
        assert "x\\|y" in page.content and "line1<br>line2" in page.content
        assert "w" * 500 not in page.content and "…" in page.content

    def test_ragged_rows_get_extra_columns(self, tmp_path):
        src = tmp_path / "x.csv"
        src.write_text("a,b\n1,2,3\n4\n", encoding="utf-8")
        page = tr.read_table(str(src))
        assert page.columns == ["a", "b", "column_3"]
        assert "| 2 | 4 |  |  |" in page.content


class TestPaging:
    def _large(self, tmp_path, rows=5000):
        src = tmp_path / "large.csv"
        body = "".join(f"{i};Kunde {i};{i},00 €\r\n" for i in range(1, rows + 1))
        src.write_bytes(("Nr;Kunde;Betrag\r\n" + body).encode("cp1252"))
        return src

    def test_row_count_and_offset(self, tmp_path):
        src = self._large(tmp_path)
        page = tr.read_table(str(src), offset=101, limit=50)
        assert page.total_rows == 5000
        assert (page.first_row, page.last_row) == (101, 150)
        assert "| 101 | 101 | Kunde 101 |" in page.content
        assert "| 151 |" not in page.content
        assert page.truncated and "offset=151" in page.hint
        # The header repeats on every page.
        assert page.content.startswith("| # | Nr | Kunde | Betrag |")

    def test_char_budget_bounds_the_page(self, tmp_path):
        src = self._large(tmp_path)
        page = tr.read_table(str(src), limit=2000, max_chars=4000)
        assert len(page.content) <= 4000
        assert page.last_row < 2000 and f"offset={page.last_row + 1}" in page.hint

    def test_offset_past_the_end(self, tmp_path):
        page = tr.read_table(str(self._large(tmp_path, rows=3)), offset=10)
        out = page.to_dict()
        assert out["rows_shown"] == "none" and out["total_rows"] == 3
        assert "past the last row" in out["warnings"][0]

    def test_oversized_file_reports_a_lower_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tr, "MAX_TABLE_BYTES", 2000)
        page = tr.read_table(str(self._large(tmp_path)), limit=10)
        out = page.to_dict()
        assert out["total_rows_is_lower_bound"] is True and out["truncated"] is True
        assert 0 < out["total_rows"] < 5000

    def test_binary_is_refused(self, tmp_path):
        src = tmp_path / "x.csv"
        src.write_bytes(b"PK\x03\x04\x00\x00")
        with pytest.raises(tr.TableReadError):
            tr.read_table(str(src))


class TestReadFileTool:
    def test_windows_export_reads_as_table(self, tmp_path):
        src = _cp1252_csv(tmp_path / "WJ4School_2026_Abrechnung(Abrechnung).csv")
        out = json.loads(read_file_tool(str(src)))
        assert "error" not in out, out
        assert out["format"] == "table" and out["encoding"] == "cp1252"
        assert out["delimiter"] == "semicolon" and out["total_rows"] == 2
        assert "Müller" in out["content"] and "1.234,50 €" in out["content"]
        assert "�" not in out["content"]

    def test_offset_limit_page_rows(self, tmp_path):
        src = TestPaging()._large(tmp_path, rows=1200)
        out = json.loads(read_file_tool(str(src), offset=1001, limit=500))
        assert out["total_rows"] == 1200 and out["rows_shown"] == "1001-1200"
        assert out["truncated"] is False and "hint" not in out

    def test_large_table_stays_bounded(self, tmp_path):
        src = TestPaging()._large(tmp_path, rows=20000)
        out = json.loads(read_file_tool(str(src), limit=2000))
        assert "error" not in out
        assert len(out["content"]) <= 40_000
        assert out["total_rows"] == 20000 and out["truncated"] is True
        assert out["hint"].startswith("Use offset=")

    def test_remote_sandbox_keeps_the_text_read(self, tmp_path, monkeypatch):
        src = _cp1252_csv(tmp_path / "a.csv")
        import tools.file_tools as ft

        real = ft._get_file_ops

        def remote_ops(task_id="default"):
            ops = real(task_id)
            monkeypatch.setattr(type(ops), "_lsp_local_only", lambda self: False, raising=False)
            return ops

        monkeypatch.setattr(ft, "_get_file_ops", remote_ops)
        out = json.loads(read_file_tool(str(src)))
        assert "format" not in out and "total_lines" in out

    def test_unparseable_table_falls_back_to_text(self, tmp_path, monkeypatch):
        src = tmp_path / "a.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")

        def boom(*a, **k):
            raise tr.TableReadError("nope")

        monkeypatch.setattr(tr, "read_table", boom)
        out = json.loads(read_file_tool(str(src)))
        assert "format" not in out and "1,2" in out["content"]

    def test_xlsx_still_goes_through_document_conversion(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Name", "Betrag"])
        ws.append(["Müller", "12,50 €"])
        wb.save(str(tmp_path / "t.xlsx"))
        out = json.loads(read_file_tool(str(tmp_path / "t.xlsx")))
        assert "error" not in out, out
        assert out["converter"].startswith("local-")
        assert "Müller" in out["content"] and "|" in out["content"]

    def test_schema_names_the_table_capability(self):
        from tools.file_tools import READ_FILE_SCHEMA

        desc = READ_FILE_SCHEMA["description"]
        assert "CSV/TSV" in desc and "Markdown table" in desc and "terminal" in desc


def test_reference_text_decodes_windows_exports(tmp_path):
    from agent.context_references import _read_reference_text

    assert "Müller" in _read_reference_text(_cp1252_csv(tmp_path / "a.csv"))
