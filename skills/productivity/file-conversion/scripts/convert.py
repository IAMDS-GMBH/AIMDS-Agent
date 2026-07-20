#!/usr/bin/env python3
"""Convert files between formats.

Usage:
    python convert.py input.docx --to pdf
    python convert.py input.md --to docx
    python convert.py input.md --to html
    python convert.py input.xlsx --to csv
    python convert.py input.csv --to xlsx
    python convert.py input.json --to csv
    python convert.py input.yaml --to json
    python convert.py input.json --to yaml
    python convert.py input.html --to pdf
    python convert.py image.heic --to jpg
    python convert.py image.png --to webp
    python convert.py logo.svg --to png
    python convert.py video.mov --to mp4
    python convert.py audio.m4a --to mp3
    python convert.py folder/ --to pdf     # batch-convert all .docx in folder
"""

import subprocess
import sys
from pathlib import Path


def _libreoffice_cmd():
    for candidate in ["libreoffice", "soffice",
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice"]:
        r = subprocess.run(["which", candidate] if "/" not in candidate
                           else ["test", "-x", candidate], capture_output=True)
        if r.returncode == 0:
            return candidate
    return None


def _docx_to_pdf_fpdf2(src: Path, out: Path):
    """Text-only DOCX → PDF via fpdf2 (no images, preserves headings/paragraphs).

    PITFALL: must call set_margins() BEFORE add_page(), and use at least 20mm
    margins. The default 10mm margins can cause "Not enough horizontal space to
    render a single character" FPDFException on narrow cells.
    """
    from docx import Document
    from fpdf import FPDF

    doc = Document(str(src))
    pdf = FPDF(format='A4')
    pdf.set_margins(20, 20, 20)          # must be set before add_page()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            pdf.ln(4)
            continue
        sname = para.style.name
        if "Heading 1" in sname:
            pdf.set_font("Helvetica", "B", 18)
            pdf.ln(4)
        elif "Heading 2" in sname:
            pdf.set_font("Helvetica", "B", 14)
            pdf.ln(3)
        elif "Heading 3" in sname:
            pdf.set_font("Helvetica", "B", 12)
            pdf.ln(2)
        else:
            pdf.set_font("Helvetica", size=11)
        # fpdf2 v2+ handles UTF-8 natively — no manual encoding needed
        pdf.multi_cell(0, 7, text)
        pdf.ln(1)
    pdf.output(str(out))
    print(f"PDF (text-only via fpdf2): {out}")


def office_to_pdf(src: Path, out: Path):
    """docx/xlsx/pptx → pdf. Tries docx2pdf → LibreOffice → fpdf2 (docx only)."""
    # 1. docx2pdf
    if src.suffix.lower() == ".docx":
        try:
            from docx2pdf import convert
            convert(str(src), str(out))
            if out.exists():
                print(f"PDF: {out}")
                return
            print("docx2pdf ran but produced no file (Word may not be installed), trying next...", file=sys.stderr)
        except ImportError:
            pass
        except Exception as e:
            print(f"docx2pdf failed ({e}), trying next...", file=sys.stderr)

    # 2. LibreOffice
    lo = _libreoffice_cmd()
    if lo:
        result = subprocess.run(
            [lo, "--headless", "--convert-to", "pdf", str(src), "--outdir", str(out.parent)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out.exists():
            print(f"PDF: {out}")
            return
        print(f"LibreOffice failed: {result.stderr}", file=sys.stderr)

    # 3. fpdf2 fallback (docx only)
    if src.suffix.lower() == ".docx":
        try:
            _docx_to_pdf_fpdf2(src, out)
            return
        except ImportError as e:
            print(f"fpdf2 fallback unavailable: {e}", file=sys.stderr)

    print(
        f"ERROR: Cannot convert {src.suffix} to PDF. Install one of:\n"
        "  - LibreOffice  →  https://www.libreoffice.org\n"
        "  - docx2pdf     →  uv pip install docx2pdf  (DOCX + Word only)",
        file=sys.stderr,
    )
    sys.exit(1)


def to_markdown(src: Path, out: Path):
    r = subprocess.run(
        ["pandoc", str(src), "-o", str(out)], capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"Markdown: {out}")
        return
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(src))
    out.write_text(result.text_content, encoding="utf-8")
    print(f"Markdown: {out} (via markitdown)")


def pandoc_convert(src: Path, out: Path, extra=None):
    cmd = ["pandoc", str(src), "-o", str(out)]
    if extra:
        cmd.extend(extra)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"pandoc error: {r.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"{out.suffix.upper().lstrip('.')}: {out}")


def html_to_pdf(src: Path, out: Path):
    from weasyprint import HTML

    HTML(filename=str(src)).write_pdf(str(out))
    print(f"PDF: {out}")


def csv_to_xlsx(src: Path, out: Path):
    import pandas as pd

    pd.read_csv(src).to_excel(str(out), index=False)
    print(f"XLSX: {out}")


def xlsx_to_csv(src: Path, out: Path, sheet=None):
    import pandas as pd

    pd.read_excel(str(src), sheet_name=sheet or 0).to_csv(
        str(out), index=False, encoding="utf-8-sig"
    )
    print(f"CSV: {out}")


def json_to_csv(src: Path, out: Path):
    import pandas as pd

    pd.read_json(str(src)).to_csv(str(out), index=False)
    print(f"CSV: {out}")


def csv_to_json(src: Path, out: Path):
    import pandas as pd

    pd.read_csv(str(src)).to_json(str(out), orient="records", indent=2)
    print(f"JSON: {out}")


def yaml_to_json(src: Path, out: Path):
    import json

    import yaml

    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON: {out}")


def json_to_yaml(src: Path, out: Path):
    import json

    import yaml

    data = json.loads(src.read_text(encoding="utf-8"))
    out.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8"
    )
    print(f"YAML: {out}")


def image_convert(src: Path, out: Path):
    suffix = src.suffix.lower()
    if suffix == ".heic":
        import pillow_heif

        pillow_heif.register_heif_opener()
    if suffix == ".svg":
        import cairosvg

        if out.suffix.lower() == ".png":
            cairosvg.svg2png(url=str(src), write_to=str(out))
        elif out.suffix.lower() == ".pdf":
            cairosvg.svg2pdf(url=str(src), write_to=str(out))
        else:
            print(f"SVG can only be converted to png or pdf", file=sys.stderr)
            sys.exit(1)
    else:
        from PIL import Image

        Image.open(src).save(str(out))
    print(f"{out.suffix.upper().lstrip('.')}: {out}")


def ffmpeg_convert(src: Path, out: Path, extra=None):
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if extra:
        cmd.extend(extra)
    cmd.append(str(out))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"{out.suffix.upper().lstrip('.')}: {out}")


def batch_convert(folder: Path, fmt: str):
    """Batch-convert all supported Office files in a folder to the target format."""
    folder = folder.resolve()
    patterns = {
        "pdf": ["*.docx", "*.xlsx", "*.pptx"],
        "md": ["*.docx", "*.pptx", "*.pdf"],
    }
    globs = patterns.get(fmt, [f"*.{fmt}"])
    files = [f for g in globs for f in folder.glob(g)]
    if not files:
        print(f"No matching files found in {folder}")
        return
    for f in files:
        out = f.with_suffix(f".{fmt}")
        dispatch(f, out, fmt)


def dispatch(src: Path, out: Path, fmt: str):
    src_ext = src.suffix.lower().lstrip(".")
    image_exts = {
        "jpg",
        "jpeg",
        "png",
        "webp",
        "bmp",
        "gif",
        "tiff",
        "tif",
        "heic",
        "svg",
    }
    video_exts = {"mp4", "mov", "avi", "mkv", "webm", "flv"}
    audio_exts = {"mp3", "wav", "m4a", "aac", "ogg", "flac"}
    office_exts = {"docx", "xlsx", "pptx", "doc", "xls", "ppt"}

    if src_ext in office_exts and fmt == "pdf":
        office_to_pdf(src, out)
    elif src_ext in office_exts and fmt in ("md", "markdown"):
        to_markdown(src, out)
    elif src_ext in ("md", "rst", "txt") and fmt in ("docx", "pdf", "html"):
        pandoc_convert(src, out, ["--standalone"] if fmt == "html" else None)
    elif src_ext == "html" and fmt == "pdf":
        html_to_pdf(src, out)
    elif src_ext in ("docx", "pptx") and fmt == "html":
        pandoc_convert(src, out, ["--standalone"])
    elif src_ext == "csv" and fmt in ("xlsx", "xls"):
        csv_to_xlsx(src, out)
    elif src_ext in ("xlsx", "xls") and fmt == "csv":
        xlsx_to_csv(src, out)
    elif src_ext == "json" and fmt == "csv":
        json_to_csv(src, out)
    elif src_ext == "csv" and fmt == "json":
        csv_to_json(src, out)
    elif src_ext == "yaml" and fmt == "json":
        yaml_to_json(src, out)
    elif src_ext == "json" and fmt in ("yaml", "yml"):
        json_to_yaml(src, out)
    elif src_ext in image_exts or fmt in image_exts:
        image_convert(src, out)
    elif src_ext in video_exts or fmt in video_exts:
        ffmpeg_convert(src, out)
    elif src_ext in audio_exts or fmt in audio_exts:
        ffmpeg_convert(src, out)
    else:
        print(f"No handler for {src_ext} → {fmt}. Try pandoc or ffmpeg directly.")
        sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        sys.exit(0)

    src = Path(args[0]).resolve()
    fmt = None
    if "--to" in args:
        fmt = args[args.index("--to") + 1].lower().lstrip(".")

    if not fmt:
        print("Specify target format with --to <format>")
        sys.exit(1)

    if src.is_dir():
        batch_convert(src, fmt)
    else:
        out = src.with_suffix(f".{fmt}")
        dispatch(src, out, fmt)
