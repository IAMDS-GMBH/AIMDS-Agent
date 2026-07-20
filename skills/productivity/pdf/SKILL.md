---
name: pdf
description: "Create, merge, split, compress, sign, watermark, and manipulate PDF files."
version: 1.1.0
author: aimds
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Documents, Productivity, Merge, Split, Forms]
    related_skills: [ocr-and-documents, nano-pdf, word, file-conversion]
---

# PDF Skill — Full PDF Manipulation

Use this skill for **creating, combining, splitting, compressing, watermarking,
password-protecting, or filling forms** in PDF files.

- For **text extraction / OCR** from existing PDFs → use `ocr-and-documents`
- For **editing small text typos** in PDFs → use `nano-pdf`
- For **converting other formats to PDF** → use `file-conversion`

## Script: scripts/pdf.py

```
python3 scripts/pdf.py --merge a.pdf b.pdf c.pdf --out merged.pdf
python3 scripts/pdf.py --split report.pdf --out pages/
python3 scripts/pdf.py --extract report.pdf --pages 2-6 --out excerpt.pdf
python3 scripts/pdf.py --compress large.pdf --out small.pdf
python3 scripts/pdf.py --watermark doc.pdf "CONFIDENTIAL" --out watermarked.pdf
python3 scripts/pdf.py --protect doc.pdf --user-pwd read123 --owner-pwd admin456 --out protected.pdf
python3 scripts/pdf.py --unlock protected.pdf --pwd read123 --out unlocked.pdf
python3 scripts/pdf.py --rotate scanned.pdf 90 --out fixed.pdf
python3 scripts/pdf.py --metadata report.pdf
python3 scripts/pdf.py --fill form.pdf --fields "name=Max,date=2026-06-10" --out filled.pdf
```

## Prerequisites

```bash
uv pip install pypdf pymupdf fpdf2
```

## Quick decision guide

| Task | Flag |
|------|------|
| Combine PDFs | `--merge` |
| Split into pages | `--split` |
| Extract a page range | `--extract --pages 2-6` |
| Reduce file size | `--compress` |
| Add diagonal text stamp | `--watermark` |
| Password-protect | `--protect` |
| Remove password | `--unlock` |
| Rotate all pages | `--rotate 90` |
| View author/title/pages | `--metadata` |
| Fill AcroForm fields | `--fill --fields "k=v,k2=v2"` |

## Common pitfalls

- **Encrypted PDFs**: decrypt before any operation with `--unlock` first.
- **Form fields after merge**: fields flatten when pages are merged; fill forms before merging.
- **Scanned PDFs**: `--compress` won't help much if pages are images — use `ocr-and-documents` to get a text layer first.
