---
name: customer-crm-lookup
description: Schneller und gezielter Abruf von Kundendaten, Verträgen und Ansprechpartnern aus go-mcp-customer / Qdrant.
---

# Customer CRM Lookup

## Zweck & Vorgehen
1. **Kundensuche:** Führe BM25/Vektorsuche mit `storage_search({"query":"<Kundenname> <Thema>"})` aus.
2. **Dokumente & Historie lesen:** Rufe Zusammenfassungen mit `storage_meta({"kind":"summary","doc_id":"..."})` oder Abschnitte via `storage_get_document` ab.
3. **Kontext abgleichen:** Verknüpfe Kundenergebnisse mit M365 Kalender- oder E-Mail-Kontexten.
4. **Verzeichnis-Synchronisation:** Speichere Kundenexzerpte bei Bedarf unter `~/Documents/AIMDS-Suite-Vault/contacts/<Kundenname>.md`.
