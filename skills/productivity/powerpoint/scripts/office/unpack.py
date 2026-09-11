"""Unpack a DOCX/PPTX/XLSX file into a directory with pretty-printed XML.

Usage:
    python unpack.py <office_file> <output_directory>

Counterpart of pack.py: extracts the OOXML zip and re-indents every .xml/.rels
part so slides/sheets are diffable and hand-editable. Run clean.py and pack.py
afterwards to produce a valid file again.
"""

import sys
import zipfile
from pathlib import Path

import defusedxml.minidom


def _pretty_print(xml_file: Path) -> None:
    try:
        dom = defusedxml.minidom.parse(str(xml_file))
    except Exception as exc:  # leave non-XML parts untouched
        print(f"Warning: could not pretty-print {xml_file.name}: {exc}", file=sys.stderr)
        return
    xml_file.write_bytes(dom.toprettyxml(indent="  ", encoding="UTF-8"))


def unpack(office_file: str, output_dir: str) -> str:
    src = Path(office_file)
    dst = Path(output_dir)
    if not src.is_file():
        return f"Error: {src} is not a file"
    if src.suffix.lower() not in {".docx", ".pptx", ".xlsx"}:
        return f"Error: {src} must be a .docx, .pptx, or .xlsx file"
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(dst)
    for pattern in ("*.xml", "*.rels"):
        for part in dst.rglob(pattern):
            _pretty_print(part)
    return f"Unpacked {src} to {dst}"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python unpack.py <office_file> <output_directory>", file=sys.stderr)
        sys.exit(1)
    message = unpack(sys.argv[1], sys.argv[2])
    print(message)
    if message.startswith("Error"):
        sys.exit(1)
