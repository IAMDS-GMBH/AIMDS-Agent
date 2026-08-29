---
name: customer-crm-lookup
description: Fast, targeted retrieval of customer CRM data, contracts, and contact persons from go-mcp-customer / Qdrant, cross-referenced with Microsoft 365 calendar and email context.
---

# Customer CRM Lookup

## Purpose & procedure
1. **Customer search:** Run a BM25/vector search with `storage_search({"query":"<CustomerName> <Topic>"})`.
2. **Read documents & history:** Fetch summaries with `storage_meta({"kind":"summary","doc_id":"..."})` or sections via `storage_get_document`.
3. **Cross-reference context:** Link customer results with M365 calendar or email context.
4. **Directory sync:** When needed, store customer excerpts at `~/Documents/AIMDS-Suite-Vault/contacts/<CustomerName>.md`.
