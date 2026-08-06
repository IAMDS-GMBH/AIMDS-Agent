---
name: digest
description: Erstellt eine wiederkehrende Zusammenfassung (Tages- oder Wochen-Digest) aus Kalender, Posteingang, offenen Aufgaben und weiterem verfügbarem Projektkontext. Als Cron-Blueprint nutzbar (morning-brief / weekly-digest).
metadata:
  hermes:
    blueprint:
      name: morning-brief
      fields: [uhrzeit]
      default_schedule: "0 8 * * 1-5"
---

# Digest

## Vorgehen
1. **Sprachberücksichtigung:** In der Sprache des Benutzers antworten (Deutsch falls Kontext/System deutsch ist, sonst Englisch).
2. **Daten tool-basiert sammeln:**
   - Verfügbare Kalender-, Mail- und Aufgaben-Tools (`email-triage`, `PLAN.md`, etc.) nutzen.
   - **MSOffice365MCP Integration:** Falls MSOffice365MCP aktiv und verbunden ist (Outlook, Teams, OneDrive, SharePoint), Termine, Mails und Teams-Updates für die Arbeitswoche automatisch abfragen.
3. **Zeitraum einschränken:** Fokus strikt auf die aktuelle Arbeitswoche (Montag-Freitag). Über das Wochenende hinaus nur den nächsten Werktag (Montag) berücksichtigen.
4. **Notizen für morgen vorbereiten:** Kurze Notizen/Highlights im Workspace/Memory für den nächsten Werktag ablegen, damit Fragen wie "Was steht morgen an?" sofort beantwortet werden können.
5. **Priorisieren:** max. **3 Dinge, die heute zählen** (Hard Cap), dann Rest.
6. **Kompakt liefern:**
   - Was heute/diese Woche zählt (max 3)
   - Termine & M365 Kalender
   - Wichtige Mails & Teams-Nachrichten (mit Handlungsbedarf)
   - Offene Aufgaben & Ausblick auf den nächsten Werktag
7. **Pflicht für Weekly Review:** bei wöchentlichem Lauf enthalten:
   - wichtigste Ergebnisse dieser Woche,
   - Carry-over-Punkte,
   - Top-3-Prioritäten für nächste Woche,
   - Inaktive Projekte (>=14 Tage Inaktivität),
   - Risiken/Offene Fragen mit Entscheidungsbedarf (falls Entscheidung fehlt: `OPEN_QUESTION_NEEDED: ...`).
8. **Ruhig bleiben:** Wenn nichts Relevantes → kurz "Nichts Dringendes" statt Lärm.

## Ausgabe-Format
Ton, Status-Marker und Struktur folgen `guardrails/output-format.md`
(Tages-Briefing bzw. Wochen-Rückblick). Der Wochenlauf nutzt den ausführlichen
`weekly-digest`-Skill, sofern vorhanden.

## Verifikation
- Top-3 sind wirklich die wichtigsten, nicht die ersten besten.
- Keine erledigten Punkte als offen gemeldet.

## Was NICHT
- Kein Wall-of-Text. Keine Mails automatisch beantworten — nur berichten.
