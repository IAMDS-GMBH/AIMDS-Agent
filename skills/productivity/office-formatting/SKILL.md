---
name: office-formatting
description: Standard formatting guidelines for Microsoft Word (.docx) and Excel (.xlsx) documents using IAMDS Corporate Identity (colors, fonts, tables, headers, layout). Trigger when creating, editing, or formatting Word or Excel documents.
---

# IAMDS Office Document Formatting (Word & Excel)

This skill defines the official styling, formatting, and layout standards for Word documents and Excel workbooks created or edited by Hermes Agent.

## 1. IAMDS Corporate Identity & Color Palette
Always apply IAMDS brand colors when styling titles, headers, table headers, and highlights:

| Element | Color Name | HEX Code | RGB | Usage |
|---|---|---|---|---|
| **Primary Brand** | IAMDS Blau | `#3F59FF` | (63, 89, 255) | Main titles, primary table headers, main buttons |
| **Accent / Highlight** | IAMDS Gold | `#FFD440` | (255, 212, 64) | Callout boxes, highlight cells, key metrics |
| **Header Accent** | Dunkelblau | `#1C30B2` | (28, 48, 178) | Document title banners, dark section dividers |
| **Dark Background** | Nachtblau | `#212B80` | (33, 43, 128) | Table header fills on executive summaries |
| **Light Fill** | Fast-Weiss | `#F2F5FC` | (242, 245, 252) | Alternating table rows (zebra striping), callout fills |
| **Subtle Border** | Hellgrau | `#E0E3FC` | (224, 227, 235) | Gridlines, table borders, card borders |
| **Body Text** | Fast-Schwarz | `#212121` | (33, 33, 33) | Default text color (never use pure 000000) |

## 2. Typography Rules
- **Font Family**: `Roboto` (or `Arial` / `Calibri` as standard Office fallback).
- **Headlines**: Bold or Medium weight, Dunkelblau (`#1C30B2`) or Fast-Schwarz (`#212121`).
- **Body Text**: Regular weight, Fast-Schwarz (`#212121`), 10.5–11 pt, 1.15 line spacing.

## 3. Word Document Formatting (.docx)
- **Document Title**: 24–28 pt Bold, IAMDS Blau (`#3F59FF`).
- **Heading 1**: 16–18 pt Bold, Dunkelblau (`#1C30B2`), with a 1.5 pt bottom border or accent bar.
- **Heading 2**: 13–14 pt SemiBold, Fast-Schwarz (`#212121`).
- **Tables**:
  - Header Row: Dark fill (`#3F59FF` or `#212B80`), White text (`#FFFFFF`), Bold.
  - Alternating Rows: Light zebra fill (`#F2F5FC`).
  - Borders: Thin Hellgrau (`#E0E3FC`).
  - Padding: At least 6 pt top/bottom padding for clean readability.
- **Callout & Key Takeaway Boxes**:
  - Left border: 3 pt IAMDS Gold (`#FFD440`) or IAMDS Blau (`#3F59FF`).
  - Background fill: Fast-Weiss (`#F2F5FC`).

## 4. Excel Workbook Formatting (.xlsx)
- **Sheet Headers**:
  - Row 1–2: Title in 16 pt Bold, IAMDS Blau (`#3F59FF`), subtitle in 10 pt Dunkelgrau (`#7A7A80`).
- **Table Headers**:
  - Header Fill: IAMDS Blau (`#3F59FF`) or Nachtblau (`#212B80`), White text, Bold, centered/aligned.
- **Data Rows**:
  - Alternating row shading using Fast-Weiss (`#F2F5FC`).
  - Font: 10 pt Regular (`Roboto` / `Calibri`).
- **Number Formatting**:
  - Currency: `€#,##0.00` or `€#,##0`
  - Percentages: `0.0%`
  - Dates: `YYYY-MM-DD` or `DD.MM.YYYY`
- **Total / Summary Rows**:
  - Top thin border, bottom double border.
  - Bold text, total values highlighted with light IAMDS Gold (`#FFF5C2`) fill or bold accent.
- **Auto-fit Columns**: Always auto-fit column widths so no text is truncated or shows `###`.

## 5. Automated Generation Tools
- When producing Word docs, use `python-docx` or pandoc with proper style definitions.
- When producing Excel workbooks, use `openpyxl` or `xlsxwriter` applying border, fill, and font objects matching the color palette above.
