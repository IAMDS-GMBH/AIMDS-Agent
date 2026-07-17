---
name: excel
description: "Create, read, edit, and analyse Microsoft Excel (.xlsx) files."
version: 1.1.0
author: aimds
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Excel, XLSX, Spreadsheet, Office, Data, Productivity]
    related_skills: [word, powerpoint, file-conversion]
---

# Excel Skill — Microsoft Excel (.xlsx / .xls / .csv)

Use this skill for spreadsheet tasks: reading data, creating reports, editing
cells, building charts, or exporting to CSV/PDF.

## Scripts

### scripts/read.py — Read and analyse

```
python3 scripts/read.py workbook.xlsx                    # Print all data
python3 scripts/read.py workbook.xlsx --sheet "Q2"       # Specific sheet
python3 scripts/read.py workbook.xlsx --sheets           # List all sheet names
python3 scripts/read.py workbook.xlsx --stats            # Summary statistics
python3 scripts/read.py workbook.xlsx --cell B5          # Single cell value
python3 scripts/read.py workbook.xlsx --metadata         # Creator, dates, sheets
```

### scripts/write.py — Create and edit

```
python3 scripts/write.py --from-csv data.csv output.xlsx           # CSV → XLSX
python3 scripts/write.py --set-cell B5 "=SUM(B2:B4)" report.xlsx  # Set cell
python3 scripts/write.py --set-cell B5 42 report.xlsx --sheet Q2  # Specific sheet
python3 scripts/write.py --append-row "April,120000,85000" report.xlsx
python3 scripts/write.py --to-csv report.xlsx output.csv           # Export to CSV
python3 scripts/write.py --to-pdf report.xlsx                      # Export to PDF
```

## Prerequisites

```bash
uv pip install openpyxl pandas
```

## Quick decision guide

| Task | Command |
|------|---------|
| Inspect a workbook | `read.py` + `--sheets` then `--sheet` |
| Get summary stats | `read.py --stats` |
| Load CSV into Excel | `write.py --from-csv` |
| Update a cell / formula | `write.py --set-cell` |
| Add a new row | `write.py --append-row` |
| Export to CSV | `write.py --to-csv` |
| Export to PDF | `write.py --to-pdf` (requires LibreOffice) |

## Common pitfalls

- **Cached formulas**: `read.py` uses `data_only=True` — returns last cached result. If the file was never opened in Excel, formula cells return `None`.
- **Merged cells**: merged ranges only store the value in the top-left cell; other cells return `None`.
- **Date serial numbers**: openpyxl returns Python `datetime` objects automatically; pandas handles them too.
