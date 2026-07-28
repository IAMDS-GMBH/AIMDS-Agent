---
name: morgen-briefing-agent
description: Automatisiertes Morgen-Briefing (Cron 08:00 Uhr) zur Erstellung der Tagesagenda mit Kalender, Mails und Kunden-Kontext.
---

# Morgen-Briefing Agent (Cron)

## Arbeitsweise & Cronjob-Ablauf
Dieser Agent läuft jeden Morgen (z. B. 08:00 Uhr via Hermes Cron Execution) im Hintergrund ab:

1. **Kalender-Check:** Rufe die heutigen Termine mit `m365-calendar-planner` ab.
2. **Posteingang-Scan:** Analysiere ungelesene E-Mails mit `m365-mail-assistant`.
3. **CRM-Abgleich:** Hole bei Kundenterminen relevante Infos mit `customer-crm-lookup`.
4. **Tagesjournal erstellen:** Speichere die strukturierte Tagesagenda unter `~/Documents/AIMDS-Suite-Vault/journal/YYYY-MM-DD-Tagesagenda.md`.
5. **Kurz-Benachrichtigung:** Erstelle eine prägnante Zusammenfassung im Chat oder Teams.
