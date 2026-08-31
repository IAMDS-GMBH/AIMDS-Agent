---
name: doc-ingest-analyze
description: Two-stage ad-hoc document upload to go-mcp-customer with automatic text extraction (Docling/native), search indexing, and a vault note; use to ingest PDF, DOCX, XLSX, PPTX, ODT, ODS, or ODP files for later search — including tabular documents like vacation lists or timesheets.
---

# Document Ingest & Analysis

## Purpose
Enables fast, memory-efficient upload of ad-hoc documents to `go-mcp-customer` without raw Base64 bytes burdening the LLM context window.

**Supported types (enforced by the server): PDF, DOCX, XLSX, PPTX, ODT, ODS, ODP — max 20 MiB.**
TXT and CSV are NOT accepted. A local CSV/TXT can simply be read directly (no upload needed); if it must live in the store, ask the user for an XLSX export instead.

## The 4-step flow

```
[1. Prepare] ──────────> [2. HTTP Upload] ─────────> [3. Ingest & Index] ─────> [4. Vault Note]
 storage_ingest_upload    POST file bytes            storage_ingest_upload       store the note at
 ({})                     Authorization: Bearer      ({"upload_id": "..."})      documents/<Name>.md
```

### Step 1: Prepare
Call `storage_ingest_upload({})` (no arguments).
The result returns `upload_url`, the supported file types, and size limits.

### Step 2: HTTP POST Upload
Send the raw file bytes directly via HTTP POST to the returned `upload_url`, passing the user's LiteLLM Virtual Key in the `Authorization: Bearer <key>` header.

### Step 3: Ingest & indexing
Call `storage_ingest_upload({"upload_id": "<upload_id>"})`.
`go-mcp-customer` automatically performs:
- Text extraction via Docling (markdown; tables become pipe-tables) or native fallback
- One embedding per document for hybrid search (BM25 + vector, stored server-side in Postgres)
- TF-IDF topic computation and creation of a summary

Note: search (`storage_search`) finds *documents*, never individual rows — there is exactly one embedding per document, so a question like "which days are in this list?" must be answered from the full text, not from search.

### Step 4: Store the document note in the vault
Create a Markdown note at `~/Documents/AIMDS-Suite-Vault/documents/<DocumentName>.md` following this schema:
```markdown
---
title: "<Document title>"
type: document
doc_id: "<document_id>"
created: YYYY-MM-DDTHH:MM:SS
tags:
  - import
---

# <Document title>

## Summary
<Extracted summary from storage_meta(kind="summary", doc_id=...)>

## Storage reference
- Document ID: `<document_id>`
- Provider: `upload`
```

## Tabular documents (vacation lists, timesheets, schedules)
Tables survive extraction only as markdown pipe-tables inside the full text. Rules:
1. **Always fetch the whole document**: `storage_get_document({"id": "<doc_id>", "format": "markdown"})`. NEVER use the word-window chunk reads for tabular content — the 500-word windows cut through table rows and drop headers.
2. Parse the pipe-tables into day-level data yourself. Disambiguate date formats explicitly (German DD.MM.YYYY vs. MM/DD); Excel cells arrive as formatted display strings, not typed values.
3. **Vacation/absence days extracted from a document belong in the `absences` table**: `workdays(action='absences', op='add', days=[…], source='document:<doc_id>')`. Record in the vault note how many days were taken over and from which document.
4. A locally available file (vault, filesystem) can be read directly without the upload round-trip — same `absences` bridge applies.
