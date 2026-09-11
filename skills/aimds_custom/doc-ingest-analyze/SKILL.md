---
name: doc-ingest-analyze
description: Read Office and PDF documents (DOCX, XLSX, PPTX, ODT, ODS, ODP, PDF) as Markdown with read_file, and optionally ingest them into the AIMDS-Suite document store (go-mcp-customer, Docling extraction) so they become searchable; covers tabular documents like vacation lists or timesheets.
---

# Document Reading & Ingest

## Reading a document (default)
Call `read_file(<path>)` on the file. Office files and PDFs are converted to Markdown
automatically — by the AIMDS-Suite Docling when the Suite is reachable
(`/uptime/health` reports `docling` and `customer-storage` up and the storage tools are
available), otherwise by the local converters. The result carries `converter`
(`suite-docling` or `local-*`) and `cache_path`. Use `offset`/`limit` for long documents.

Rules:
- Never parse a document with terminal commands (unzip, strings, python one-liners).
- Paths returned by the M365 download tools (`saved_path`) are passed to `read_file` as-is.
- Legacy `.doc`/`.xls`/`.ppt` are not supported — ask for a modern export or a PDF.
- A `.pdf` with `converter: local-*` and almost no text is probably scanned; OCR needs the Suite.

## Making a document searchable in the Suite (optional)
Only when `storage_ingest_upload` is available in the session. It uploads the file into
the user's own document catalog in `go-mcp-customer` (per-key isolation), extracts text
via Docling (or the native fallback), computes one embedding per document and a summary.

```
[1. Prepare] ──────────> [2. Upload] ───────────────> [3. Ingest & Index] ─────> [4. Vault Note]
 storage_ingest_upload    POST file to upload_url      storage_ingest_upload       documents/<Name>.md
 ({})                     Authorization: Bearer key    ({"upload_id": "..."})
```

Supported types (enforced by the server): PDF, DOCX, XLSX, PPTX, ODT, ODS, ODP — max 20 MiB.
`read_file` already performs steps 1-3 when it converts through the Suite; a second ingest of
the same file is unnecessary. If the file was converted locally, run steps 1-3 explicitly:
step 2 is a plain HTTP multipart POST (field `file`) with the same `Authorization: Bearer`
key the client uses for LiteLLM — never send raw base64 through a tool parameter.

### Vault note (step 4)
Create `~/Documents/AIMDS-Suite-Vault/documents/<DocumentName>.md`:
```markdown
---
title: "<Document title>"
type: document
doc_id: "upload:<upload_id>"
created: YYYY-MM-DDTHH:MM:SS
tags:
  - import
---

# <Document title>

## Summary
<summary from storage_meta(kind="summary", doc_id=...)>

## Storage reference
- Document ID: `upload:<upload_id>`
- Provider: `upload`
```

Note: `storage_search` finds *documents*, never individual rows — exactly one embedding per
document — so a question like "which days are in this list?" is answered from the full text.

## Tabular documents (vacation lists, timesheets, schedules)
Tables survive as Markdown pipe-tables in the full text. Rules:
1. Read the whole table: `read_file` with a wide enough `limit`, or
   `storage_get_document({"id": "upload:<id>", "format": "tables"})` for ingested documents.
   Never rely on 500-word chunk reads for tabular content — they cut rows and drop headers.
2. Parse the pipe-tables into day-level data yourself. Disambiguate date formats explicitly
   (German DD.MM.YYYY vs. MM/DD); Excel cells arrive as formatted display strings.
3. Vacation/absence days extracted from a document belong in the `absences` table:
   `workdays(action='absences', op='add', days=[…], source='document:<doc_id>')`. Record in the
   vault note how many days were taken over and from which document.
