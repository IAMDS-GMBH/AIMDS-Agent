---
name: m365-calendar-planner
description: Anforderung von Kalenderevents, Terminfindung, Konfliktprüfung und Vorbereitung von Meeting-Kontexten aus E-Mails und CRM.
---

# M365 Calendar Planner

## Zweck & Vorgehen
1. **Kalender abfragen:** Rufe Termine mit `m365_get_events` ab.
2. **Konflikte identifizieren:** Überprüfe Überschneidungen und Vorbereitungsfenster.
3. **Meeting-Kontext vorbereiten:**
   - Suche relevante E-Mails via `m365_list_emails(query=...)`.
   - Suche Kundeninformationen in `go-mcp-customer` via `storage_search(query=...)`.
4. **Vorbereitungsnotiz ablegen:** Speichere Meeting-Briefings direkt unter `~/Documents/AIMDS-Suite-Vault/meetings/YYYY-MM-DD-Meeting-<Thema>.md`.
