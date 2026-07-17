---
name: word
description: "Create, read, edit, and convert Microsoft Word (.docx) files."
version: 1.1.0
author: aimds
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Word, DOCX, Office, Documents, Productivity]
    related_skills: [powerpoint, ocr-and-documents, pdf, file-conversion]
---

# Word Skill — Microsoft Word (.docx)

Use this skill for any task involving `.docx` or `.doc` files.
For `.pptx` use the `powerpoint` skill. For PDFs use `pdf` or `ocr-and-documents`. For cross-format conversion use `file-conversion`.

## Scripts

### scripts/read.py — Extract content

```
python3 scripts/read.py document.docx              # Plain text + tables
python3 scripts/read.py document.docx --markdown   # Markdown via markitdown
python3 scripts/read.py document.docx --tables     # Tables only
python3 scripts/read.py document.docx --metadata   # Title, author, page count
python3 scripts/read.py document.docx --styles     # List styles in use
```

### scripts/write.py — Create and edit

```
python3 scripts/write.py --from-md input.md output.docx        # Markdown → DOCX
python3 scripts/write.py --replace "Old" "New" document.docx   # Find & replace
python3 scripts/write.py --append "New paragraph." document.docx
python3 scripts/write.py --merge a.docx b.docx c.docx --out merged.docx
```

### scripts/convert.py — Export to other formats

```
python3 scripts/convert.py document.docx --to pdf
python3 scripts/convert.py document.docx --to md
python3 scripts/convert.py document.docx --to html
python3 scripts/convert.py reports/ --to pdf        # Convert entire folder
```

## Prerequisites

```bash
uv pip install python-docx markitdown docx2pdf
# For Markdown → DOCX / HTML: brew install pandoc  (or apt/winget)
```

## Quick decision guide

| Task | Command |
|------|---------|
| Extract text for analysis | `read.py --markdown` |
| Read table data | `read.py --tables` |
| Markdown → Word | `write.py --from-md` |
| Bulk find & replace | `write.py --replace` |
| Combine multiple files | `write.py --merge` |
| Word → PDF | `convert.py --to pdf` |
| Word → Markdown | `convert.py --to md` |

## Common pitfalls

- **PDF conversion**: `--to pdf` uses `docx2pdf` (requires Word on macOS/Windows) then falls back to LibreOffice. For Linux always use LibreOffice.
- **Styles are locale-sensitive**: built-in names like `"List Bullet"` differ in German Word. Use `read.py --styles` to inspect what's available.
- **Find & replace splits runs**: Word may split a word across multiple runs. If `--replace` misses something, use `read.py --markdown` first to confirm the exact text.
