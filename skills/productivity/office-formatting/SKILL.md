---
name: office-formatting
description: Standard formatting guidelines for Microsoft Word (.docx) and Excel (.xlsx) documents using customizable Corporate Identity themes (colors, fonts, tables, headers, layout). Trigger when creating, editing, or formatting Word or Excel documents.
---

# Office Document Formatting (Word & Excel)

This skill defines styling, formatting, and layout standards for Word documents (.docx) and Excel workbooks (.xlsx) generated or edited by Hermes Agent. It supports customizable Corporate Identity (CI) color palettes.

## 1. Corporate Identity & Color Palettes

### Option A: Standard Corporate Palette (Default)
When no specific brand guideline is requested, use this clean professional palette:

| Element | Color Name | HEX Code | Usage |
|---|---|---|---|
| **Primary Brand** | Corporate Blue | `#0052CC` | Titles, primary table headers, accent borders |
| **Accent / Highlight** | Accent Gold | `#FFB800` | Callout borders, highlight cells, key metrics |
| **Dark Header** | Deep Slate | `#172B4D` | Executive table headers, dark section banners |
| **Light Fill** | Soft Ice | `#F4F5F7` | Alternating zebra rows, callout background fills |
| **Border / Grid** | Light Neutral | `#DFE1E6` | Subtle table borders, dividers |
| **Body Text** | Off-Black | `#253858` | Default text color (avoid pure #000000) |

### Option B: IAMDS Corporate Palette
Use when creating documents specifically for IAMDS:

| Element | Color Name | HEX Code | Usage |
|---|---|---|---|
| **Primary Brand** | IAMDS Blau | `#3F59FF` | Titles, primary table headers |
| **Accent / Highlight** | IAMDS Gold | `#FFD440` | Callout boxes, highlight cells |
| **Header Accent** | Dunkelblau | `#1C30B2` | Banners, section dividers |
| **Dark Header** | Nachtblau | `#212B80` | Executive summary headers |
| **Light Fill** | Fast-Weiss | `#F2F5FC` | Zebra striping, callout fills |
| **Subtle Border** | Hellgrau | `#E0E3FC` | Gridlines, borders |
| **Body Text** | Fast-Schwarz | `#212121` | Main body text |

### Option C: Custom Repository / Client CI
If the workspace contains a `.ci.json`, `ci-guide.md`, or brand guidelines in repository settings, extract primary, accent, header, and light fill colors from that file and apply them consistently across all generated documents.

## 2. Typography Rules
- **Font Family**: Standard clean corporate fonts (`Calibri`, `Arial`, or `Roboto`).
- **Headlines**: Bold or Medium weight, Dark Header color or Primary Brand color.
- **Body Text**: Regular weight, Off-Black (`#253858` or `#212121`), 10.5–11 pt, 1.15 line spacing.

## 3. Word Document Formatting (.docx)
- **Document Title**: 24–28 pt Bold, Primary Brand color.
- **Heading 1**: 16–18 pt Bold, Dark Header color, with a 1.5 pt bottom border or accent bar.
- **Heading 2**: 13–14 pt SemiBold, Off-Black text.
- **Tables**:
  - Header Row: Dark/Primary fill, White text (`#FFFFFF`), Bold.
  - Alternating Rows: Light zebra fill (Soft Ice / Fast-Weiss).
  - Borders: Thin Light Neutral border.
  - Padding: At least 6 pt top/bottom cell margins for clean readability.
- **Callout & Key Takeaway Boxes**:
  - Left border: 3 pt Accent color or Primary Brand color.
  - Background fill: Soft Ice / Light fill.

## 4. Excel Workbook Formatting (.xlsx)
- **Sheet Headers**:
  - Row 1–2: Title in 16 pt Bold (Primary Brand color), subtitle in 10 pt muted gray.
- **Table Headers**:
  - Header Fill: Primary Brand or Dark Header color, White text, Bold, centered/aligned.
- **Data Rows**:
  - Alternating row shading using Soft Ice fill.
  - Font: 10 pt Regular (`Calibri` / `Arial`).
- **Number Formatting**:
  - Currency: `€#,##0.00` or `$#,##0.00`
  - Percentages: `0.0%`
  - Dates: `YYYY-MM-DD` or `DD.MM.YYYY`
- **Total / Summary Rows**:
  - Top thin border, bottom double border.
  - Bold text, total values highlighted with light Accent fill.
- **Auto-fit Columns**: Always auto-fit column widths so no text is truncated or displays `###`.

## 5. Automated Generation Tools
- **Word (.docx)**: Use `python-docx` or pandoc with configured style objects.
- **Excel (.xlsx)**: Use `openpyxl` or `xlsxwriter` with custom `PatternFill`, `Font`, and `Border` definitions matching the chosen CI palette.
