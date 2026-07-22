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
1. **Daten zuerst tool-basiert holen:** verfügbare Kalender-, Mail- und
   Aufgaben-Tools/Quellen nutzen, um heutige/wöchentliche Termine, wichtige
   neue Mails (via `email-triage`-Logik) und offene To-Dos / `PLAN.md`-Stände
   zu sammeln. Relevanten Zusatzkontext (z. B. Kanban, Commits, Notizen)
   ebenfalls einbeziehen.
2. **Priorisieren:** max. **3 Dinge, die heute zählen** (Hard Cap), dann Rest.
3. **Kompakt liefern:**
   - Was heute/diese Woche zählt (max 3)
   - Termine
   - Wichtige Mails (nur die, die Handlung brauchen)
   - Offene Aufgaben
4. **Pflicht für Weekly Review:** bei wöchentlichem Lauf enthalten:
   - wichtigste Ergebnisse dieser Woche,
   - Carry-over-Punkte,
   - Top-3-Prioritäten für nächste Woche.
5. **Ruhig bleiben:** Wenn nichts Relevantes → kurz "nichts Dringendes" statt Lärm.

## Verifikation
- Top-3 sind wirklich die wichtigsten, nicht die ersten besten.
- Keine erledigten Punkte als offen gemeldet.

## Was NICHT
- Kein Wall-of-Text. Keine Mails automatisch beantworten — nur berichten.
