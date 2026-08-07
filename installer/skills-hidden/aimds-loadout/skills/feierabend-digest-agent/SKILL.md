---
name: feierabend-digest-agent
description: Automatisiertes Feierabend-Digest (Cron 17:00 Uhr) zur Zusammenfassung des Tages, erledigter Aufgaben und Entwürfe.
---

# Feierabend-Digest Agent (Cron)

## Arbeitsweise & Cronjob-Ablauf
Dieser Agent läuft zum Arbeitstag-Ende (z. B. 17:00 Uhr via Hermes Cron Execution) im Hintergrund ab:

1. **Aufgaben-Review:** Prüfe erledigte & offene To-Dos aus `~/Documents/AIMDS-Suite-Vault/tasks/`.
2. **Mail- & Entwurfs-Status:** Erfasse erstellte E-Mail-Entwürfe zur Freigabe.
3. **Tages-Zusammenfassung:** Erstelle den Feierabend-Digest unter `journal/YYYY-MM-DD-feierabend-digest.md` im aktiven Workspace.

## Ausgabe-Format
Ton, Status-Marker und Struktur folgen `guardrails/output-format.md`. Bei ruhigem
Tag kurz "Nichts Offenes" statt einer erfundenen Liste.
