---
name: doc-ingest-analyze
description: Two-stage ad-hoc document upload to go-mcp-customer / Qdrant with automatic text extraction, vector indexing, and a vault note; use to ingest PDF, DOCX, XLSX, or TXT files for later customer and CRM search.
---

# Document Ingest & Qdrant Analysis

## Purpose
Enables fast, memory-efficient upload of ad-hoc documents (PDF, DOCX, XLSX, TXT) to `go-mcp-customer` / Qdrant without raw Base64 bytes burdening the LLM context window.

## The 4-step flow

```
[1. Prepare] ──────────> [2. HTTP Upload] ─────────> [3. Ingest & Qdrant] ─────> [4. Vault Note]
 storage_ingest_upload    POST file bytes            storage_ingest_upload        store the note at
 ({})                     Authorization: Bearer      ({"upload_id": "..."})       documents/<Name>.md
```

### Step 1: Prepare
Call `storage_ingest_upload({})` (no arguments).
The result returns `upload_url`, the supported file types, and size limits.

### Step 2: HTTP POST Upload
Send the raw file bytes directly via HTTP POST to the returned `upload_url`, passing the user's LiteLLM Virtual Key in the `Authorization: Bearer <key>` header.

### Step 3: Ingest & Qdrant indexing
Call `storage_ingest_upload({"upload_id": "<upload_id>"})`.
`go-mcp-customer` automatically performs:
- Text and structure extraction (via Docling / native)
- Generation of vector embeddings in Qdrant
- TF-IDF topic computation and creation of a summary

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
  - qdrant
---

# <Document title>

## Summary
<Extracted summary from storage_meta(kind="summary", doc_id=...)>

## Qdrant vector reference
- Document ID: `<document_id>`
- Provider: `upload`
```
