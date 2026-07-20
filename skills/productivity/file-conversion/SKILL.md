---
name: file-conversion
description: "Convert files between formats: Office ↔ PDF, Markdown ↔ HTML/DOCX, images, audio/video, and more."
version: 1.1.0
author: aimds
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Conversion, PDF, Office, Markdown, Images, Video, Audio, Pandoc, FFmpeg, Productivity]
    related_skills: [word, excel, pdf, ocr-and-documents, powerpoint]
---

# File Conversion Skill

Use this skill whenever a file needs to change format. For deep editing of the
resulting file use the dedicated skill (`word`, `excel`, `pdf`).

## Script: scripts/convert.py

```
python3 scripts/convert.py input.docx --to pdf
python3 scripts/convert.py input.md --to docx
python3 scripts/convert.py input.md --to html
python3 scripts/convert.py input.xlsx --to csv
python3 scripts/convert.py input.csv --to xlsx
python3 scripts/convert.py input.json --to csv
python3 scripts/convert.py input.yaml --to json
python3 scripts/convert.py input.json --to yaml
python3 scripts/convert.py input.html --to pdf
python3 scripts/convert.py image.heic --to jpg
python3 scripts/convert.py image.png --to webp
python3 scripts/convert.py logo.svg --to png
python3 scripts/convert.py video.mov --to mp4
python3 scripts/convert.py audio.m4a --to mp3
python3 scripts/convert.py folder/ --to pdf        # batch: all .docx in folder
```

## Prerequisites

```bash
uv pip install markitdown docx2pdf pypdf weasyprint Pillow cairosvg pillow-heif pandas pyyaml
brew install pandoc ffmpeg          # macOS
# apt install pandoc ffmpeg         # Linux
# winget install pandoc ffmpeg      # Windows
```

## Format matrix

| From | To | Notes |
|------|----|-------|
| docx / xlsx / pptx | pdf | docx2pdf (needs Word) → LibreOffice fallback |
| md / rst / txt | docx / pdf / html | pandoc |
| html | pdf | weasyprint |
| docx / pptx / pdf | md | pandoc → markitdown fallback |
| csv | xlsx | pandas |
| xlsx | csv | pandas |
| json ↔ csv | | pandas |
| yaml ↔ json | | pyyaml |
| heic / jpg / png / webp | any image | Pillow (+ pillow-heif for HEIC) |
| svg | png / pdf | cairosvg |
| mov / avi / mkv | mp4 | ffmpeg |
| m4a / wav / ogg | mp3 | ffmpeg |

## Common pitfalls

- **LibreOffice path on macOS**: if `libreoffice` is not in PATH, full path is `/Applications/LibreOffice.app/Contents/MacOS/soffice`. Add an alias or symlink.
- **HEIC on Linux**: install system library `libheif` in addition to `pillow-heif`.
- **pandoc DOCX quality**: use a reference template for professional output: `pandoc input.md -o out.docx --reference-doc=template.docx`.
- **ffmpeg codec availability**: `libx264` requires a full FFmpeg build (`brew install ffmpeg` includes it; some apt packages do not).
