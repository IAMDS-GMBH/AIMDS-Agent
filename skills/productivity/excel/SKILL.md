---
name: excel
description: "Create, read, edit, and analyse Microsoft Excel (.xlsx) files."
version: 1.2.0
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
python3 scripts/read.py workbook.xlsx                    # First 200 rows (truncation is announced)
python3 scripts/read.py workbook.xlsx --max-rows 50      # Cap rows printed
python3 scripts/read.py workbook.xlsx --range A1:D20     # Exact block as TSV
python3 scripts/read.py workbook.xlsx --sheet "Q2"       # Specific sheet
python3 scripts/read.py workbook.xlsx --sheets           # List all sheet names
python3 scripts/read.py workbook.xlsx --stats            # Summary statistics
python3 scripts/read.py workbook.xlsx --cell B5          # Single cell value
python3 scripts/read.py workbook.xlsx --metadata         # Creator, dates, sheets
```

### scripts/write.py — Create and edit

```
python3 scripts/write.py --new report.xlsx --sheets "Data,Summary"          # Empty workbook
python3 scripts/write.py --from-csv data.csv output.xlsx                    # CSV → XLSX
python3 scripts/write.py --set-cell B5 "=SUM(B2:B4)" report.xlsx           # Set one cell
python3 scripts/write.py --set-cells '{"A1":"Region","B1":"Revenue","B2":1200.5}' report.xlsx   # Many cells, one save
python3 scripts/write.py --append-row 'April,"1,200",85000' report.xlsx    # Real CSV parsing
python3 scripts/write.py --append-json '["April", 1200, 85000]' report.xlsx # Typed values
python3 scripts/write.py --format '{"range":"A1:C1","bold":true,"fill":"3F59FF","font_color":"FFFFFF","autofit":true}' report.xlsx
python3 scripts/write.py --format '{"range":"B2:B50","number_format":"#,##0.00 €"}' report.xlsx
python3 scripts/write.py --to-csv report.xlsx output.csv                    # Export to CSV
python3 scripts/write.py --to-pdf report.xlsx                               # Export to PDF (LibreOffice)
```

Inside Hermes prefer the `office_excel` tool: actions `read_sheet` (`max_rows`,
`range`), `list_sheets`, `stats`, `read_cell`, `metadata`, `create`, `from_csv`,
`set_cell`, `set_cells`, `append_row` (`values[]` or `row_csv`), `format_cells`
(`range` + `style`), `to_csv`, `to_pdf`. Apply the palettes from the
`office-formatting` skill via `format_cells`.

## Prerequisites

```bash
uv pip install -e ".[office]"        # openpyxl, pandas, python-docx, python-pptx, markitdown (pinned)
# Optional: LibreOffice for --to-pdf
```

## Quick decision guide

| Task | Command |
|------|---------|
| Inspect a workbook | `read.py` + `--sheets` then `--sheet` |
| Get summary stats | `read.py --stats` |
| Load CSV into Excel | `write.py --from-csv` |
| Update a cell / formula | `write.py --set-cell` |
| Update many cells | `write.py --set-cells` (JSON) |
| Add a new row | `write.py --append-row` / `--append-json` |
| Style headers / number formats | `write.py --format` (JSON) |
| Export to CSV | `write.py --to-csv` |
| Export to PDF | `write.py --to-pdf` (requires LibreOffice) |

## Common pitfalls

- **Cached formulas**: `read.py` uses `data_only=True` — returns last cached result. If the file was never opened in Excel, formula cells return `None`.
- **Merged cells**: merged ranges only store the value in the top-left cell; other cells return `None`.
- **Date serial numbers**: openpyxl returns Python `datetime` objects automatically; pandas handles them too.
- **Large sheets**: `read.py` stops after 200 rows and says so — use `--range` for a specific block instead of raising the cap blindly.
- **Commas in values**: quote them in `--append-row` (`"1,200"`) or use `--append-json`; numbers with thousands separators stay strings.
