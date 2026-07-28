---
name: doc-ingest-analyze
description: Zweistufiger Ad-hoc Dokumentenupload an go-mcp-customer / Qdrant mit automatischer Text-Extraktion, Vektor-Indexierung und Vault-Notiz.
---

# Document Ingest & Qdrant Analysis

## Zweck
Ermöglicht den schnellen und speichereffizienten Upload von Ad-hoc-Dokumenten (PDF, DOCX, XLSX, TXT) an `go-mcp-customer` / Qdrant, ohne dass rohe Base64-Bytes das LLM-Kontextfenster belasten.

## Der 4-Schritt-Ablauf

```
[1. Prepare] ──────────> [2. HTTP Upload] ─────────> [3. Ingest & Qdrant] ─────> [4. Vault Note]
 storage_ingest_upload    POST file bytes            storage_ingest_upload        Notiz ablegen in
 ({})                     Authorization: Bearer      ({"upload_id": "..."})       documents/<Name>.md
```

### Schritt 1: Prepare
Rufe `storage_ingest_upload({})` (ohne Argumente) auf.
Ergebnis liefert `upload_url`, unterstützte Dateitypen und Größenlimits.

### Schritt 2: HTTP POST Upload
Übermittle die rohen Datei-Bytes direkt per HTTP POST an die erhaltene `upload_url` mit dem LiteLLM Virtual Key des Nutzers im `Authorization: Bearer <key>` Header.

### Schritt 3: Ingest & Qdrant Indexierung
Rufe `storage_ingest_upload({"upload_id": "<upload_id>"})` auf.
`go-mcp-customer` führt automatisch aus:
- Text- & Struktur-Extraktion (via Docling / Native)
- Erzeugung von Vektor-Embeddings in Qdrant
- TF-IDF Themenberechnung & Erstellung einer Zusammenfassung

### Schritt 4: Dokumentennotiz im Vault speichern
Erstelle eine Markdown-Notiz unter `~/Documents/AIMDS-Suite-Vault/documents/<Dokumentenname>.md` mit folgendem Schema:
```markdown
---
title: "<Dokumententitel>"
type: document
doc_id: "<document_id>"
created: YYYY-MM-DDTHH:MM:SS
tags:
  - import
  - qdrant
---

# <Dokumententitel>

## Zusammenfassung
<Extrahierte Zusammenfassung aus storage_meta(kind="summary", doc_id=...)>

## Qdrant Vektor-Referenz
- Document ID: `<document_id>`
- Provider: `upload`
```
