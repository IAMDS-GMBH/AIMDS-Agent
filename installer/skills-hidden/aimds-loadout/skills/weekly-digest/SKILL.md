---
name: weekly-digest
description: Wochen-Rückblick (Cron, z.B. Fr 16:00) — sammelt die Woche aus Aufgaben, Findings und aktiven Projekten und liefert einen vorbereiteten Rückblick (Was lief / Was offen blieb / Insights / Plan nächste Woche). Nutzen für "Wochenrückblick", "Weekly Review".
metadata:
  hermes:
    blueprint:
      name: weekly-digest
      fields: [wochentag, uhrzeit]
      default_schedule: "0 16 * * 5"
---

# Weekly Digest (Wochen-Rückblick)

## Rolle
Du bereitest den Wochen-Rückblick vor: sammle, was diese Woche passiert ist, und liefere
einen kompakten Rückblick, den der Nutzer nur noch um eigene Reflexion ergänzen muss.
Daten sammeln, nicht schönfärben — ehrlich bei offenen Punkten.

## Phase 1 — Daten sammeln
1. `tasks/thisweek.md` — was war geplant, was ist erledigt, was blieb offen.
2. `tasks/tasks.md` — was aus dem Backlog diese Woche relevant wurde.
3. `_findings.md` — die wichtigsten Fundstücke der Woche.
4. `projects/` — **nur `projectStatus: active` oder `waiting`** (siehe
   `guardrails/project-lifecycle.md`). Pro Projekt: letzte Änderung (`updated:`), offene
   Punkte, grober Fortschritt. `waiting` nur als Status-Reminder, nie als Aktionspunkt.
   `dormant`/`done`/`parked` bleiben außen vor. Projektstand zählt der `projectStatus`,
   nicht die Zahl offener Punkte.
5. `journal/` — den letzten Wochen-Rückblick für Kontext (was war letzte Woche der Plan?).

## Phase 2 — Rückblick bauen
Struktur und Ton folgen `guardrails/output-format.md`, Abschnitt "Wochen-Rückblick":

```
# Wochen-Rückblick KW YYYY-WXX

## Was lief diese Woche
- ✅ … (max 5)

## Was offen blieb
- 🔴 … (max 5)

## Insights (0–3, optional)
- …

## Plan nächste Woche
1. Top-Priorität
2. …
3. …
```

- **HARD CAP 5 Items pro Sektion.** Der Rest wandert als Backlink nach `_findings.md` —
  keine Sektion überlaufen lassen.
- **Insights nur wenn echte da sind.** Null Insights ist ein gültiges Ergebnis; nichts
  erzwingen, um die Sektion zu füllen.
- **Plan nächste Woche** ist ein **Vorschlag** (aus offenen Punkten + fälligen Projekt-
  Prioritäten), klar als Vorschlag gekennzeichnet — der Nutzer entscheidet.

## Phase 3 — Ablegen & liefern
- Rückblick nach `journal/YYYY-WXX-review.md` schreiben (dort designiert `AGENTS.md`
  Wochen-Reviews). Frontmatter nach `_conventions.md` (`type`, `title`, `created`,
  `updated`). Existiert der Eintrag schon, erweitern statt doppeln.
- Eine kompakte Fassung in die Ausgabe (Chat bzw. Cron-Antwort).
- Optional anbieten (nicht still tun): `tasks/thisweek.md` für die nächste Woche
  vorbereiten — erledigte Items nach unten, offene übernehmen, vorgeschlagene Top-3 oben.

## Verifikation
- [ ] Nur `active`/`waiting`-Projekte im Rückblick, `waiting` nur als Reminder?
- [ ] Keine Sektion über 5 Items?
- [ ] Insights nur, wenn wirklich welche da sind — sonst Sektion weggelassen?
- [ ] "Plan nächste Woche" als Vorschlag markiert, kein eigenmächtiger Statuswechsel?
- [ ] Nichts erfunden — keine Erfolge oder Findings, die nicht belegt sind?
- [ ] Bei ruhiger Woche ehrlich kurz statt künstlich gefüllt?

## Was NICHT
- Keine Zeiterfassungs-, Vertriebs- oder Delegations-Sektion (firmenspezifisch).
- Projekt-Status nicht selbst ändern — nur vorschlagen (`guardrails/project-lifecycle.md`).
- Kein Schönfärben offener Punkte.
