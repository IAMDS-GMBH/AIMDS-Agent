---
name: meeting-prep
description: Erstellt ein kompaktes Briefing zu einem anstehenden Termin aus Kalender, relevanten Dokumenten und aktueller Web-/Firmeninfo. Nutzen vor Meetings, Kundenterminen, Calls.
metadata:
  hermes:
    blueprint:
      name: meeting-prep
      fields: [vorlaufzeit]
      default_schedule: "0 7 * * 1-5"
---

# Meeting Prep

## Vorgehen
1. **Termin holen:** aus dem Kalender (Titel, Zeit, Teilnehmer, Beschreibung).
2. **Kontext sammeln:** relevante Mails/Dokumente; bei Personen/Firmen kurz
   recherchieren (`deep-research`-Logik); Firmeninternes via **KB** (`kb_search`).
3. **Briefing bauen** (kurz, scanbar):
   - Wer/Was/Wann + Ziel des Termins
   - 3-5 Talking Points
   - mögliche Fragen/Einwände + Antworten
   - offene Punkte / was der Nutzer mitbringen muss

## Ausgabe-Format
Ton und Status-Marker folgen `guardrails/output-format.md`. Briefing scanbar
halten, keine Wall-of-Text.

## Verifikation
- Teilnehmer & Zeit stimmen mit dem Kalender überein.
- Keine erfundenen Fakten über Personen/Firmen — nur Belegtes.

## Was NICHT
- Keine privaten/sensiblen Daten über Teilnehmer aus unsicheren Quellen.

## Als Cron-Blueprint
Läuft über die `blueprint`-Metadaten im Frontmatter (Default werktags 07:00, Feld
`vorlaufzeit`) — der Nutzer aktiviert ihn per `/blueprint meeting-prep`. Siehe
`blueprints/README.md`.
