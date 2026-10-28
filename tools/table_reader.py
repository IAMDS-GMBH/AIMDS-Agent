"""Delimited table files (CSV/TSV) as a paged Markdown table for ``read_file``.

AIS-600 (SUP-20261013-161622): a Windows CSV export (cp1252, ``;``) came
back from the line-based ``read_file`` path with every umlaut and € replaced
by U+FFFD, so the model reached for ``pandas`` in the terminal — and the
approval prompt timed out. Reading a table needs no terminal: the encoding is
decided on the bytes (BOM → strict UTF-8 → cp1252 → latin-1), the delimiter
from the content, and the result is a bounded Markdown table with the row
count and row paging. Stdlib only, deterministic.
"""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

#: Extension → fixed delimiter (``None`` = detect from the content).
TABLE_EXTENSIONS: dict[str, Optional[str]] = {".csv": None, ".tsv": "\t", ".tab": "\t"}

#: Candidate delimiters, in tie-break order.
_DELIMITERS = (";", ",", "\t", "|")
_DELIMITER_NAMES = {"\t": "tab", ";": "semicolon", ",": "comma", "|": "pipe"}

#: Bytes parsed at most; a larger file is previewed from its first part and
#: the row count is reported as a lower bound.
MAX_TABLE_BYTES = 64 * 1024 * 1024
#: Character budget for one page of Markdown (the model's context, not the file).
MAX_PAGE_CHARS = 40_000
#: One cell is clipped beyond this many characters.
MAX_CELL_CHARS = 200
#: Rows inspected to pick the delimiter.
_SNIFF_ROWS = 50

_SEP_LINE_RE = re.compile(r"^sep=(.)\s*$", re.IGNORECASE)


class TableReadError(Exception):
    """The file is not a readable delimited table (caller falls back to text)."""


@dataclass
class TablePage:
    content: str
    encoding: str
    delimiter: str
    columns: list[str]
    total_rows: int
    total_rows_exact: bool
    first_row: int
    last_row: int
    file_size: int
    truncated: bool
    hint: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "content": self.content,
            "format": "table",
            "encoding": self.encoding,
            "delimiter": _DELIMITER_NAMES.get(self.delimiter, self.delimiter),
            "columns": self.columns,
            "total_rows": self.total_rows,
            "rows_shown": f"{self.first_row}-{self.last_row}" if self.last_row >= self.first_row else "none",
            "file_size": self.file_size,
            "truncated": self.truncated,
        }
        if not self.total_rows_exact:
            out["total_rows_is_lower_bound"] = True
        if self.hint:
            out["hint"] = self.hint
        if self.warnings:
            out["warnings"] = self.warnings
        return out


def is_table_file(path: str | os.PathLike[str]) -> bool:
    return os.path.splitext(str(path))[1].lower() in TABLE_EXTENSIONS


def detect_encoding(raw: bytes) -> str:
    """BOM first, then a strict UTF-8 decode, then cp1252, latin-1 last."""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    for encoding in ("utf-8", "cp1252"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def decode_text(raw: bytes) -> tuple[str, str]:
    """``(text, encoding)`` for bytes of unknown encoding (never raises)."""
    encoding = detect_encoding(raw)
    try:
        return raw.decode(encoding), encoding
    except UnicodeDecodeError:  # truncated UTF-16 and the like
        return raw.decode(encoding, errors="replace"), encoding


def _consistency_score(sample: str, delimiter: str) -> Optional[tuple[float, int]]:
    try:
        rows = [r for r in csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter) if any(c.strip() for c in r)]
    except csv.Error:
        return None
    rows = rows[:_SNIFF_ROWS]
    if not rows:
        return None
    counts = Counter(len(r) for r in rows)
    width, hits = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if width < 2:
        return None
    return hits / len(rows), width


def detect_delimiter(sample: str) -> str:
    """The candidate that splits the first rows into the most consistent width.

    Consistency beats width, so a ``;`` export with decimal commas ("1,50")
    stays ``;``. ``csv.Sniffer`` decides only when no candidate yields two
    or more columns; a single-column file falls back to ``,``.
    """
    best: Optional[tuple[tuple[float, int, int], str]] = None
    for index, delimiter in enumerate(_DELIMITERS):
        score = _consistency_score(sample, delimiter)
        if score is None:
            continue
        key = (score[0], score[1], -index)
        if best is None or key > best[0]:
            best = (key, delimiter)
    if best is not None:
        return best[1]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITERS)).delimiter
    except csv.Error:
        return ","


def _cell(value: str) -> str:
    value = value.strip()
    if len(value) > MAX_CELL_CHARS:
        value = value[: MAX_CELL_CHARS - 1] + "…"
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def read_table(path: str, offset: int = 1, limit: int = 500, *, max_chars: int = MAX_PAGE_CHARS) -> TablePage:
    """Read ``path`` as a delimited table and render rows ``offset..`` as Markdown.

    ``offset`` is the 1-based data row (the header is row 0 and repeats on
    every page); ``limit`` caps the rows, ``max_chars`` the rendered page.
    Raises :class:`TableReadError` when the file cannot be read as a table.
    """
    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as fh:
            raw = fh.read(MAX_TABLE_BYTES + 1)
    except OSError as exc:
        raise TableReadError(str(exc)) from exc
    if b"\x00" in raw[:8192] and not raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise TableReadError("binary content")

    exact = len(raw) <= MAX_TABLE_BYTES
    if not exact:
        # Parse up to the last complete line; the count becomes a lower bound.
        raw = raw[: raw.rfind(b"\n", 0, MAX_TABLE_BYTES) + 1] or raw[:MAX_TABLE_BYTES]

    text, encoding = decode_text(raw)
    text = text.lstrip("\ufeff")

    warnings: list[str] = []
    delimiter = TABLE_EXTENSIONS.get(os.path.splitext(path)[1].lower())
    first_line, _, rest = text.partition("\n")
    sep_match = _SEP_LINE_RE.match(first_line.rstrip("\r"))
    if sep_match:  # Excel's "sep=;" preamble
        delimiter = sep_match.group(1)
        text = rest
    if delimiter is None:
        delimiter = detect_delimiter(text[:65536])

    try:
        rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise TableReadError(f"not a well-formed table: {exc}") from exc
    if not rows:
        header: list[str] = []
        data: list[list[str]] = []
    else:
        header, data = rows[0], rows[1:]

    total = len(data)
    start = max(1, offset)
    end_limit = min(total, start + max(1, limit) - 1)

    width = max([len(header)] + [len(r) for r in data[start - 1 : end_limit]] or [0])
    columns = [(h.strip() or f"column_{i + 1}") for i, h in enumerate(header)]
    columns += [f"column_{i + 1}" for i in range(len(columns), width)]

    head = _md_row(["#"] + [_cell(c) for c in columns]) + "\n" + _md_row(["---"] * (len(columns) + 1))
    lines = [head]
    used = len(head)
    last = start - 1
    for number in range(start, end_limit + 1):
        cells = data[number - 1] + [""] * (len(columns) - len(data[number - 1]))
        line = _md_row([str(number)] + [_cell(c) for c in cells])
        if used + len(line) + 1 > max_chars and last >= start:
            break
        lines.append(line)
        used += len(line) + 1
        last = number

    if total and start > total:
        warnings.append(f"offset {start} is past the last row ({total}).")
    truncated = last < total or not exact
    hint = None
    if last < total:
        hint = f"Use offset={last + 1} to continue (rows {start}-{last} of {total}{'+' if not exact else ''})."
    elif not exact:
        hint = (
            f"Only the first {MAX_TABLE_BYTES // (1024 * 1024)} MB of the file were parsed; "
            f"it has more than {total} rows."
        )
    return TablePage(
        content="\n".join(lines),
        encoding=encoding,
        delimiter=delimiter,
        columns=columns,
        total_rows=total,
        total_rows_exact=exact,
        first_row=start,
        last_row=last,
        file_size=file_size,
        truncated=truncated,
        hint=hint,
        warnings=warnings,
    )
