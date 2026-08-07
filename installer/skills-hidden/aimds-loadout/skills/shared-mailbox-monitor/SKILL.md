---
name: shared-mailbox-monitor
description: Konfiguriert oder führt wiederkehrende Cron-Prüfungen für Microsoft 365 Shared Mailboxes (Geteilte Postfächer) aus. Für "geteiltes Postfach prüfen", "support@domain.com überwachen" oder "Cronjob für Shared Mailbox erstellen".
---

# Shared Mailbox Monitor

## Vorgehen
1. **Shared Mailbox identifizieren:**
   - E-Mail-Adresse des geteilten Postfachs ermitteln (z. B. `support@company.com`, `vertrieb@company.com`, `info@company.com`).
   - Prüfen, ob MSOffice365MCP / `email-triage` Tools oder `msoffice365_list_messages` Zugriff auf das geteilte Postfach haben (über den `mailbox`- oder `--user-id`-Parameter).

2. **CronJob konfigurieren:**
   - Bei der Anforderung, ein geteiltes Postfach zu überwachen, einen Hermes-CronJob anlegen.
   - **Empfohlener Intervall:** Alle 1–2 Stunden während der Arbeitszeit (`0 */2 * * 1-5`).
   - **Prompt-Vertrag (Ressourcenschonend):**
     "Prüfe das geteilte Postfach <shared_mailbox_address> auf neue ungelesene oder handlungsrelevante E-Mails. Wenn keine neuen Nachrichten vorliegen, antworte 'nothing new'. Wenn neue Kunden-Mails eingegangen sind, fasse diese kompakt zusammen, hebe Handlungsbedarf hervor und benachrichtige den Benutzer."
   - Skill auf `digest` oder `email-triage` setzen.

3. **Ausführung & Triage:**
   - Ungelesene Nachrichten aus dem geteilten Postfach lesen.
   - Automatische Benachrichtigungen, Abwesenheitsnotizen und Spam herausfiltern.
   - Kundenanfragen kompakt zusammenfassen (1 Zeile pro E-Mail).
   - Bei Handlungsbedarf (z. B. Angebotsanfrage, Kundenproblem) Optionen und Antwort-Entwürfe vorbereiten (nur als Entwurf, nicht senden).

## Richtlinien
- **Niemals automatisch E-Mails versenden.** Immer Entwürfe zur Freigabe vorlegen.
- Ausgabe stumm/kurz halten ("nothing new"), wenn keine neuen Nachrichten vorliegen, um Tokens zu sparen.
- Ausgabe-Format (Marker, Ton, Zusammenfassung) folgt `guardrails/output-format.md`.
