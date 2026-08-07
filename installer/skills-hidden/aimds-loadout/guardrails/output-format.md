# Output-Format — gemeinsame Konvention für Briefings & Reports

> Single Source of Truth für **Ton, Struktur und Format** aller wiederkehrenden
> Ausgaben, die ein Skill erzeugt — Tages-Briefings, Wochen-Rückblicke,
> Run-Reports, Inbox-Bestätigungen. Damit sieht jede Ausgabe gleich aus, egal
> welcher Skill gerade läuft. Ein Skill, der eine solche Ausgabe erzeugt,
> verweist auf diese Datei statt eigene Formatregeln zu erfinden.

## 1. Tonfall — Pflicht

- **Sprache des Nutzers.** Deutsch, wenn der Kontext deutsch ist, sonst Englisch.
  Auf Deutsch informell ("Du"), aber siez-fähig — der Kunde stellt das um.
- **Direkt** — Befund zuerst, Begründung danach.
- **Ehrlich** — Sparringspartner, kein Ja-Sager. Wo ein Punkt wacklig ist, sag es.
- **Keine Floskeln** — kein "Ich hoffe das hilft", "Lass mich wissen wenn", "Spannend!".
- **Keine Emojis im Fließtext** — nur als Status-Marker (siehe unten).
- **Keine Marketing-Sprache** — kein "leveragen", "synergetisch", "ganzheitlich".

## 2. Status-Marker (Emojis nur hier)

Feste Bedeutung, immer am Zeilenanfang oder vor einem Item. Klein halten — neue
Marker nicht erfinden, sondern erst hier ergänzen, dann verwenden.

| Marker | Bedeutung | Verwendung |
|---|---|---|
| ✅ | erledigt / OK | Aufgabe erledigt, Check bestanden |
| 🔴 | hoch / kritisch / blockiert | Top-Priorität, überfällig, Blocker |
| 🟡 | mittel / wartet / Warnung | mittlere Priorität, externes Warten |
| 🟢 | optional / nice-to-have | niedrige Priorität |
| ⚪ | neutral / informativ | Hintergrund, kein Handlungsbedarf |
| ⚫ | abgesagt / verworfen | bewusst nicht zu tun |
| ⚠ | Achtung | etwas stimmt nicht, aber nicht kritisch |

## 3. Tages-Briefing — Standard-Struktur

```
# Tages-Briefing YYYY-MM-DD

## Was heute zählt (max 3)
1. …
2. …
3. …

## Wartet auf (nur als Status-Reminder)
- 🟡 …

## Fundstücke (seit dem letzten Briefing)
- …

## Heute zu erledigen (aus tasks/thisweek.md)
- …
```

- **Top-3 ist HARD CAP** — mehr als drei Prioritäten sind Lärm, nicht Service.
- "Wartet auf" nur als Reminder, nie als Aktionspunkt.
- Fundstücke: max 5 Bullet-Points, Detail bleibt in `_findings.md`.
- Bei leerem Workspace: **"Nichts Dringendes"** — keine Punkte erfinden.

## 4. Wochen-Rückblick — Standard-Struktur

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

- **HARD CAP 5 Items pro Sektion** — der Rest wandert nach `_findings.md`.
- Insights nur wenn echte da sind — nicht erzwingen. Null Insights ist gültig.

## 5. Run-Report (jeder schreibende Skill endet damit)

Ein Skill, der Dateien anlegt/ändert oder einen Cron-Lauf ausführt, schließt mit
einem kurzen Run-Report. Dieser geht in die **Ausgabe** (Chat bzw. Cron-Antwort) —
Hermes fängt Cron-Läufe über die Runtime ab. Es wird **keine** Log-Datei im
Workspace angelegt (siehe `_conventions.md`, Abschnitt "Run-Logs").

```
## Run-Report YYYY-MM-DD HH:MM

**Verarbeitet:** N Items
**Status:** ✅ alles ok  /  ⚠ X Warnungen  /  🔴 X Fehler

### Aktionen
- ✅ …
- ⚠ …

### Offene Punkte (falls vorhanden)
- …
```

**Pflichtfelder:** Verarbeitet-Anzahl und Status. Alles andere optional.
Monitoring-Läufe, die nur bei Auffälligkeit melden sollen, geben bei "nichts zu
melden" gar nichts aus (`[SILENT]`-Blueprints).

## 6. Wiki-Links

- Format `[[note-name]]` (kebab-case, ohne Pfad).
- Disambiguierung bei Bedarf mit Display-Text: `[[projekt-x|Projekt X]]`.
- In Briefing/Rückblick: jedes erwähnte Projekt / jede Person / jedes Wissen als
  Wiki-Link setzen — keine relativen Pfade.

## 7. Token-Budget pro Sektion (Richtwert)

Damit Ausgaben lesbar bleiben:

| Sektion | Richtwert (~Worte) |
|---|---|
| Tages-Briefing Top-3 | ~100 (3 × ~30) |
| Fundstücke | ~150 |
| Wochen "Was lief" | ~200 |
| Insights | ~200 |
| Run-Report | ~200 |

Bei Überlauf: Detail nach `_findings.md` auslagern, in der Ausgabe nur Backlink +
Ein-Satz-Zusammenfassung.

## 8. Was NIE in der Ausgabe steht

- "Hier ist meine Analyse" / "Im Folgenden…" — direkt einsteigen.
- "Bitte beachten Sie" — Ton ist informell (bzw. wie vom Kunden gesetzt).
- Lange Disclaimers / Hedging ("vielleicht könnte man eventuell…").
- Wiederholung des Inputs — der Nutzer weiß, was er gefragt hat.
- Aufzählungen mit nur einem Item — dann lieber als Fließtext.
