#!/usr/bin/env python3
"""Manipulate PDF files: merge, split, compress, watermark, protect, fill forms.

Usage:
    python pdf.py --merge a.pdf b.pdf c.pdf --out merged.pdf
    python pdf.py --split report.pdf --out pages/
    python pdf.py --extract report.pdf --pages 2-6 --out excerpt.pdf
    python pdf.py --compress large.pdf --out small.pdf
    python pdf.py --watermark doc.pdf "CONFIDENTIAL" --out watermarked.pdf
    python pdf.py --protect doc.pdf --user-pwd read123 --owner-pwd admin456 --out protected.pdf
    python pdf.py --unlock protected.pdf --pwd read123 --out unlocked.pdf
    python pdf.py --rotate scanned.pdf 90 --out fixed.pdf
    python pdf.py --metadata report.pdf
    python pdf.py --fill form.pdf --fields "name=Max,date=2026-06-10" --out filled.pdf
"""

import os
import sys
from pathlib import Path


def merge(paths, out):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for p in paths:
        writer.append(p)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Merged {len(paths)} files → {out}")


def split(path, out_dir):
    from pypdf import PdfReader, PdfWriter

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(path)
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        dest = out / f"page_{i + 1:03d}.pdf"
        with open(dest, "wb") as f:
            writer.write(f)
    print(f"Split {len(reader.pages)} pages → {out_dir}/")


def extract_pages(path, page_range, out):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    # parse "2-6" → [1,2,3,4,5] (0-indexed)
    if "-" in page_range:
        start, end = page_range.split("-")
        pages = range(int(start) - 1, int(end))
    else:
        pages = [int(page_range) - 1]
    for i in pages:
        if 0 <= i < len(reader.pages):
            writer.add_page(reader.pages[i])
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Extracted {len(writer.pages)} pages → {out}")


def compress(path, out):
    import fitz

    doc = fitz.open(path)
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    before = os.path.getsize(path)
    after = os.path.getsize(out)
    pct = int(100 * (1 - after / before)) if before else 0
    print(
        f"Compressed {before // 1024}KB → {after // 1024}KB ({pct}% reduction) → {out}"
    )


def watermark(path, text, out):
    import io

    from fpdf import FPDF
    from pypdf import PdfReader, PdfWriter

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 48)
    pdf.set_text_color(200, 200, 200)
    pdf.rotate(45, 100, 150)
    pdf.text(30, 160, text)
    wm_bytes = bytes(pdf.output())

    wm_page = PdfReader(io.BytesIO(wm_bytes)).pages[0]
    reader = PdfReader(path)
    writer = PdfWriter()
    for page in reader.pages:
        page.merge_page(wm_page)
        writer.add_page(page)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Watermarked → {out}")


def protect(path, user_pwd, owner_pwd, out):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt(user_password=user_pwd, owner_password=owner_pwd, permissions_flag=4)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Password-protected → {out}")


def unlock(path, pwd, out):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    if reader.is_encrypted:
        reader.decrypt(pwd)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Unlocked → {out}")


def rotate(path, degrees, out):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    for page in reader.pages:
        page.rotate(int(degrees))
        writer.add_page(page)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Rotated {degrees}° → {out}")


def show_metadata(path):
    import json

    from pypdf import PdfReader

    reader = PdfReader(path)
    meta = reader.metadata or {}
    print(
        json.dumps(
            {
                "pages": len(reader.pages),
                "title": meta.get("/Title", ""),
                "author": meta.get("/Author", ""),
                "subject": meta.get("/Subject", ""),
                "creator": meta.get("/Creator", ""),
                "producer": meta.get("/Producer", ""),
                "encrypted": reader.is_encrypted,
            },
            indent=2,
        )
    )


def fill_form(path, fields_str, out):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    writer.append(reader)
    fields = {}
    for pair in fields_str.split(","):
        k, _, v = pair.partition("=")
        fields[k.strip()] = v.strip()
    writer.update_page_form_field_values(writer.pages[0], fields)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Filled {len(fields)} field(s) → {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        sys.exit(0)

    out = args[args.index("--out") + 1] if "--out" in args else None

    if "--merge" in args:
        idx = args.index("--merge")
        paths = []
        i = idx + 1
        while i < len(args) and args[i] != "--out":
            paths.append(args[i])
            i += 1
        merge(paths, out or "merged.pdf")

    elif "--split" in args:
        path = args[args.index("--split") + 1]
        split(path, out or "pages")

    elif "--extract" in args:
        path = args[args.index("--extract") + 1]
        page_range = args[args.index("--pages") + 1] if "--pages" in args else "1"
        extract_pages(path, page_range, out or "excerpt.pdf")

    elif "--compress" in args:
        path = str(Path(args[args.index("--compress") + 1]).resolve())
        compress(path, out or str(Path(path).parent / (Path(path).stem + "_compressed.pdf")))

    elif "--watermark" in args:
        idx = args.index("--watermark")
        path = str(Path(args[idx + 1]).resolve())
        text = args[idx + 2]
        watermark(path, text, out or str(Path(path).parent / (Path(path).stem + "_watermarked.pdf")))

    elif "--protect" in args:
        path = str(Path(args[args.index("--protect") + 1]).resolve())
        user_pwd = args[args.index("--user-pwd") + 1] if "--user-pwd" in args else ""
        owner_pwd = (
            args[args.index("--owner-pwd") + 1] if "--owner-pwd" in args else user_pwd
        )
        protect(path, user_pwd, owner_pwd, out or str(Path(path).parent / (Path(path).stem + "_protected.pdf")))

    elif "--unlock" in args:
        path = str(Path(args[args.index("--unlock") + 1]).resolve())
        pwd = args[args.index("--pwd") + 1] if "--pwd" in args else ""
        unlock(path, pwd, out or str(Path(path).parent / (Path(path).stem + "_unlocked.pdf")))

    elif "--rotate" in args:
        idx = args.index("--rotate")
        path = str(Path(args[idx + 1]).resolve())
        degrees = args[idx + 2]
        rotate(path, degrees, out or str(Path(path).parent / (Path(path).stem + "_rotated.pdf")))

    elif "--metadata" in args:
        path = args[args.index("--metadata") + 1]
        show_metadata(path)

    elif "--fill" in args:
        path = str(Path(args[args.index("--fill") + 1]).resolve())
        fields_str = args[args.index("--fields") + 1] if "--fields" in args else ""
        fill_form(path, fields_str, out or str(Path(path).parent / (Path(path).stem + "_filled.pdf")))

    else:
        print("Unknown arguments. Run with --help for usage.")
        sys.exit(1)
