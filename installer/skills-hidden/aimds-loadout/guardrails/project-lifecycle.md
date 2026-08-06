# Projekt-Lifecycle — verbindliche Regel für alle Skills

> Single Source of Truth für den Umgang mit Projekt-Dateien. Jeder Skill, der eine
> Projektdatei lesen oder ändern könnte (inbox, morgen-briefing-agent, weekly-digest,
> doc-*), hält sich daran. Der Projektstatus lebt allein im Frontmatter-Feld
> `projectStatus:` — es gibt **keine** `_active`/`_backlog`/`_done`-Ordner; `projects/`
> ist flach.

## 1. Pflicht-Check vor JEDEM Schreibzugriff auf ein Projekt

Bevor ein Skill eine Datei in `projects/` anlegt oder ändert:

```
1. Datei lesen
2. Frontmatter projectStatus: extrahieren
3. Routing:
   - active   → schreiben erlaubt
   - waiting  → nicht inhaltlich updaten; nur als Status-Reminder im Briefing/Digest
   - dormant  → STOPP, nicht anfassen
   - done     → STOPP, nicht anfassen
   - parked   → STOPP, nicht anfassen
4. Wenn STOPP: den Inhalt stattdessen als Notiz/Knowledge-Eintrag mit
   `related_to: [[projekt]]`-Backlink ablegen, das Projekt bleibt unangetastet,
   und im Run-Report vermerken.
```

**Fehlt `projectStatus:` komplett:** als `active` interpretieren **und** im Run-Report
flaggen ("projectStatus fehlt bei `<datei>`"). **Kein Auto-Fix** — der Nutzer entscheidet.

## 2. Briefing-/Digest-Filter

Morgen-Briefing und Wochen-Digest übernehmen aus `projects/` **nur** Dateien mit
`projectStatus: active` oder `waiting`. `waiting` ausschließlich als **Status-Reminder**
("Projekt X wartet seit N Tagen auf Feedback"), nie als Aktionspunkt.
`dormant`/`done`/`parked` erscheinen gar nicht.

**Projektstand zählt der `projectStatus`, nicht die Zahl offener Punkte.** Ein Projekt
mit 0 offenen Punkten ist kein Befund, sondern ein gutes Zeichen. Aus wenigen offenen
Punkten nie auf "Projekt tot" schließen.

## 3. Statuswechsel — der Skill schlägt vor, vollzieht nie selbst

Ein Skill **schlägt** einen `projectStatus`-Übergang im Briefing/Digest **vor** und
führt ihn **nie eigenständig** aus — außer der Nutzer autorisiert es ausdrücklich für
einen konkreten Fall.

| Übergang | Wer | Wann (typisch) |
|---|---|---|
| `active` → `waiting` | Nutzer (Skill darf vorschlagen) | Projekt geht in externes Review/Freigabe |
| `active` → `dormant` | Nutzer | Hauptarbeit fertig, Comeback unklar |
| `active` → `done` | Nutzer | abgeschlossen, kein Comeback |
| `waiting` → `active` | Nutzer (Skill darf vorschlagen) | Feedback ist eingetroffen |
| `dormant`/`done` → `active` | Nutzer | Projekt wird wieder aufgenommen |

## 4. Pflicht-Frontmatter beim Anlegen

Jedes Projekt-Hauptdokument trägt zwingend (Vorlage: `_templates/project.md`):

```yaml
type: project
projectStatus: active | waiting | dormant | done | parked
due: "YYYY-MM-DD"     # leer lassen wenn kein Enddatum — NICHT weglassen
due-reason: ""        # Pflicht, sobald due leer ist: warum kein Enddatum
updated: "YYYY-MM-DD"
```

Skills berechnen daraus Dringlichkeit und "wartet seit N Tagen". Ein leeres `due:` ohne
`due-reason:` ist genauso stumm wie ein fehlendes — es unterscheidet nicht zwischen
"läuft bewusst ohne Enddatum" und "vergessen einzutragen". **Der Kern dieses Guardrails
ist die Durchsetzung:** die Felder existieren im Template — die Skills müssen sie beim
Anlegen füllen und ein fehlendes Feld im Run-Report flaggen statt still zu ergänzen.

## 5. Was NICHT erlaubt ist

- Eine Projektdatei mit `projectStatus: dormant`/`done`/`parked` inhaltlich editieren
  ohne ausdrücklichen Auftrag des Nutzers.
- `projectStatus` ohne expliziten Auftrag selbst ändern.
- Briefing-/Digest-Ausgabe mit Items aus `dormant`/`done`/`parked`-Projekten anreichern.
- Einen Knowledge-/Notiz-Eintrag mit `related_to: [[projekt]]` als Auslöser für ein
  Update **am Projekt** nutzen — der Backlink gehört in die Notiz, nicht ins Projekt.
