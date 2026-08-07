# Portierung: patrick-brain → AIMDS-Loadout

> **Auftrag (Patrick, 06.08.2026):** *"Ich will die Funktionen die wir hier haben
> als Geruest in den Hermes geben, was wir immer mit ausrollen koennen fuer Kunden,
> und dann von Claude Desktop hier direkt umziehen in mein Hermes."*
>
> **Bewusst KEINE gemeinsame Quelle.** Das Brain ist die Werkbank (dort wird
> erprobt), dieses Loadout ist das Produkt (das geht zum Kunden). Divergenz ist
> gewollt — was hier landet, ist die entpersonalisierte, gehaertete Fassung.
>
> Umsetzung mit Claude Code direkt in diesem Repo. Jedes Ticket ist einzeln
> abschliessbar.

---

## Die eine Regel, die alles betrifft: Entpersonalisierung

Die Brain-Agents sind auf **einen** Nutzer geschrieben. Beim Portieren MUSS raus:

| Im Brain | Im Loadout |
|---|---|
| "Patrick", "du bist Patricks zweites Gehirn" | "der Nutzer", neutrale Anrede |
| Fischi, Dominik, Gabriel, Riley | weg — oder generisch "Delegations-Empfaenger" |
| LBBW, EVN, Roechling, CruxLab, Bechtle | weg — Beispiele durch `<Kunde>` ersetzen |
| IAMDS-interne Prozesse | weg |
| `areas/career/vertriebspipeline.md` | nur wenn generisch formulierbar |
| Deutsch-informell "du" | bleibt (AIMDS-Zielgruppe), aber siezt-faehig halten |
| Pfade `tasks/thisweek.md`, `tasks/agent-queue.md` | auf `workspace-template`-Struktur mappen (siehe unten) |

**Akzeptanzkriterium fuer JEDES Ticket:** `grep -iE "patrick|fischi|lbbw|evn|iamds-intern|cruxlab|bechtle|roechling"` auf der neuen Datei liefert 0 Treffer.

## Struktur-Mapping Brain → Hermes-Workspace

| Brain | workspace-template v2 |
|---|---|
| `tasks/thisweek.md` | `tasks/thisweek.md` ✅ gleich |
| `tasks/backlog.md` | `tasks/tasks.md` |
| `tasks/agent-queue.md` | `_findings.md` |
| `_meta/agent-findings/` | `_findings.md` (eine Datei, keine Ordner) |
| `projects/_active/<p>/<p>.md` | `projects/<p>.md` (flach) |
| `projects/_backlog/`, `_done/` | ⚠ **gibt es nicht** — Status nur via `projectStatus:` |
| `people/` | `contacts/` |
| `knowledge/<domain>/` | `knowledge/` (flach) |
| `_inbox/` | `_inbox/` ✅ gleich |
| `deadline:` / `deadline-reason:` | **`due:` / `due-reason:`** ⚠ andere Feldnamen |
| `_meta/agent-logs/` | ⚠ **gibt es nicht** — Ziel definieren (Ticket 0) |

---

## Ticket 0 — Fundament (zuerst, alle anderen haengen dran)

### 0a · `workspace/AGENTS.md` fehlt
Das Loadout-README nennt `workspace/AGENTS.md` als **"Herzstueck: Routing,
Goal/PLAN-Regel, Disziplin"** — die Datei liegt aber nicht im Loadout, nur
`PLAN.template.md`. Eine Fassung existiert unter
`installer/workspace-template/AGENTS.md`.

- [x] Klaeren: soll das Loadout eine eigene AGENTS.md mitbringen oder die aus dem
      workspace-template erben? (Der `inbox`-Skill verweist auf *"Routing-Tabelle
      in `AGENTS.md` des aktiven Workspace"* — ohne die Datei laeuft er ins Leere.)
      → **Entschieden (Patrick, 06.08.): workspace-template/AGENTS.md ist die einzige
      Quelle, kein Duplikat im Loadout.**
- [x] Entsprechend anlegen oder README korrigieren. → README-Verweise korrigiert
      (Tree, 8-Schichten-Tabelle, Deploy-Schritt 4). AGENTS.md um Abschnitt
      "Session start — load context first" erweitert (aus Brain-CLAUDE.md portiert).

### 0b · Log-Ablage definieren
Die Brain-Agents schreiben Run-Logs nach `_meta/agent-logs/`. Das
workspace-template hat kein Aequivalent.

- [x] Entscheiden: `_logs/` im Workspace, oder Hermes-eigenes Logging, oder
      Run-Report nur in den Chat? → **Entschieden (Patrick, 06.08.): Run-Report in
      Chat/Cron-Output (Hermes-Runtime fängt Cron ab), KEIN `_logs/`-Ordner im Vault.**
- [x] Ergebnis in `workspace-template/_conventions.md` festhalten. → Abschnitt
      "Run-Logs — none in the workspace" ergänzt; Format in output-format.md §5.

### 0c · Geteilte Ausgabe-Konvention
Brain hat `.claude/rules/output-format.md` (Status-Marker 🔴🟡⚪, Token-Budget je
Sektion, Run-Report-Struktur, HARD-CAPs). Das Loadout hat nichts Vergleichbares —
jeder Skill formuliert eigene Ausgaben.

- [x] Als `guardrails/output-format.md` anlegen (entpersonalisiert). → angelegt:
      Tonfall, Status-Marker (generischer Satz ohne Brain-/Vertrieb-/Boulder-Marker),
      Tages-Briefing- + Wochen-Rückblick- + Run-Report-Struktur, Token-Budget.
- [x] Alle Skills darauf verweisen lassen. → globaler Hinweis in `skills/README.md`
      plus explizite Verweise in digest, feierabend-digest-agent, email-triage,
      meeting-prep, teams-triage, sharepoint-triage, shared-mailbox-monitor (die
      Rewrite-Skills morgen-briefing-agent, inbox, weekly-digest bekommen ihn in
      ihren Tickets).
- **Warum zuerst:** Ohne gemeinsame Ausgabe-Konvention sieht jeder portierte
  Skill anders aus — beim Kunden faellt genau das auf.

---

## Ticket 1 — `morgen-briefing-agent` aufwerten ⭐ groesster Hebel

**Ist:** 12 Zeilen, fuenf Bullet-Points.
**Quelle:** `patrick-brain/.claude/agents/daily-briefing-agent.md` (576 Zeilen).

Zu portierende Logik:

- [x] **Top-3-HARD-CAP** — mehr als 3 Prioritaeten sind Laerm, nicht Service.
- [x] **Frische-Check der Inbox vor dem Briefing.** → Phase 1.4: prüft `_inbox/` auf
      Items **ohne** `verarbeitet:`-Frontmatter (Hermes-Variante des Brain-Logvergleichs,
      koppelt an die Idempotenz-Marker aus Ticket 2). Bei Unverarbeitetem erst `inbox`,
      dann briefen — unabhängig von der Cron-Reihenfolge.
- [x] **projectStatus-Filter:** nur `active` und `waiting` ins Briefing.
      `waiting` **ausschliesslich als Status-Reminder**, nie als Aktionspunkt.
      `dormant`/`done`/`parked` gar nicht.
- [x] **Zonen-Struktur** statt einer flachen Liste (Was heute zaehlt / Wartet auf /
      Fundstuecke) — plus Termine/Mailbox/Liegt-zu-lange, leere Sektionen weggelassen.
- [x] **Qualitaets-Checks am Ende** (8-Punkt-Selbstprüfung vor der Ausgabe).
- [x] Ausgabe nach `guardrails/output-format.md` (Ticket 0c). Zusätzlich portiert:
      gefilterter Kalender-/Mail-Scan (nur Handlungsbedarf) und die Eskalations-Gegenprobe
      (Datum allein ist kein Befund). Nicht portiert: Vertrieb, Fischi, Lead-Erkennung,
      Zeiterfassung, Cockpit-/Landscape-Widgets, Cluster (firmenspezifisch).

**Nicht portieren:** Vertriebspipeline-Sektion, Fischi-Delegationsliste,
Lead-Erkennung — alles firmenspezifisch.

**Akzeptanz:** Briefing bei leerem Workspace erzeugt keine erfundenen Punkte,
sondern meldet "nichts Dringendes". (Im Brain der haeufigste Fehlerfall.)

---

## Ticket 2 — `inbox`-Skill haerten

**Ist:** 38 Zeilen.
**Quelle:** `patrick-brain/.claude/agents/inbox-agent.md` (836 Zeilen).

- [x] **Duplikat-Check gegen die Quell-URL, nicht gegen den Dateinamen.** → Phase 1.3:
      `url:` normalisieren, Grep über knowledge/ ideas/ projects/ contacts/ notes/
      decisions/, bei Treffer erweitern statt neu anlegen; URL muss ins Frontmatter.
- [x] **Lauf-Lock** gegen Parallellaeufe (Datei-Lock mit Verfallszeit, z.B. 30 Min).
      → Phase 0: `_inbox/.inbox-lock`, 30-Min-Verfall, verwaisten Lock überschreiben,
      am Run-Ende (auch bei Fehler) löschen.
- [x] **Kein Archivieren ohne Kern-Insight** → Phase 1.5: Gate (Ziel-Datei da +
      1–3-Satz-Insight), CTA-/Funnel-Hinweis entpersonalisiert (ohne Reel-Spezifika).
- [x] **Idempotenz:** `processing_started`-Marker vor der Verarbeitung, `error:` +
      `error_date:` ins Frontmatter statt stiller Abbruch.
- [x] **Attempt-Tracking:** `attempts:`-Zähler, ab 3 als `status: raw`-Rohnotiz ablegen
      + archivieren statt endlos retrien.
- [x] Archivieren statt loeschen (`_inbox/_archive/`), mit `verarbeitet:`-Marker (koppelt
      an den Inbox-Frische-Check des Briefings, Ticket 1).

**Nicht portieren:** Cross-Repo-Sync, Action-Tag-Routing (`#action:*`),
Video-/Reel-Analyse (siehe "Bewusst nicht" unten).

---

## Ticket 3 — Projekt-Lifecycle als Guardrail

**Quelle:** `patrick-brain/.claude/rules/project-lifecycle.md`.
Im Loadout gibt es dazu **nichts** — Skills duerfen frei in Projektdateien schreiben.

- [x] Als `guardrails/project-lifecycle.md` anlegen. → entpersonalisiert, `projects/`
      flach (kein `_active/_backlog/_done`), `due`/`due-reason` statt `deadline`.
- [x] Kern: **Vor jedem Schreibzugriff auf ein Projekt `projectStatus` lesen.**
      `active`/`waiting` → erlaubt. `dormant`/`done`/`parked` → **STOPP**,
      stattdessen als Notiz mit Rueckverweis ablegen. → §1.
- [x] **Statuswechsel schlaegt der Agent vor, vollzieht ihn nie selbst.** → §3.
- [x] Pflichtfelder beim Anlegen: `type`, `projectStatus`, `due`, `due-reason`,
      `updated`. → §4. **Anmerkung:** Das Template hatte `due-reason` NICHT — ergänzt in
      `workspace-template/_templates/project.md` (Spec sagte "hat sie bereits", stimmte
      nicht). Durchsetzung: inbox + morgen-briefing-agent verweisen auf diesen Guardrail.

---

## Ticket 4 — `weekly-digest` ausbauen

**Ist:** nur als Blueprint-Zeile in `blueprints/README.md`, kein eigener Skill.
**Quelle:** `patrick-brain/.claude/agents/weekly-review-agent.md` (142 Zeilen).

- [x] Struktur: Was lief / Was offen blieb / Insights / Plan naechste Woche. → eigener
      `weekly-digest`-Skill (vorher nur Blueprint-Zeile), folgt output-format §4.
- [x] **HARD CAP 5 Items je Sektion.**
- [x] Insights nur wenn echte da sind — nicht erzwingen (0 Insights ist gültig).
- [x] projectStatus-Filter wie Ticket 3 (nur active/waiting, waiting nur Reminder).
      Zusätzlich: Blueprint-Metadaten (Fr 16:00) gesetzt, `blueprints/README` von
      `digest` auf `weekly-digest` umgestellt, Ablage nach `journal/`.

---

## Ticket 5 — `skill-lint` fuer das Loadout

**Quelle:** `patrick-brain/automations/active/agent-lint.sh`.

Das Brain-Script prueft `.claude/agents/*.md`. Hier waere das Ziel
`skills/*/SKILL.md` — anderes Format, andere Regeln.

- [x] Erst sinnvoll **ab ~3 portierten Skills** — jetzt 20 Skills im Loadout.
- [x] Pruefbar: Frontmatter (`name`/`description`), Verweis auf `output-format.md`
      (Report-Heuristik), **Personenbezug** (identischer Entpersonalisierungs-Grep),
      tote Guardrail- und Skill-Referenzen, Cron-Angaben gegen `blueprints/README.md`.
      → `tools/skill-lint.sh`, **reines Bash** (harte Regel "kein Python"), Exit 0/1/2.
      Negativ-Test bestätigt: alle 5 Regeln feuern; Positiv-Lauf über alle 20 Skills sauber.
- [x] Das Brain-Script kennt ein `.claude/lint-profile.json` — Muster übernommen als
      `tools/lint-profile.json` (`disable` + `exempt_output_format` + `note`).

---

## Bewusst NICHT portieren

| Was | Warum |
|---|---|
| `vertrieb-agent` | Pipeline-Logik ist firmenspezifisch, kein Kundenwert |
| `fischi-briefing-agent` | personenbezogene Delegation |
| Reel-/Video-Analyse (`reel-fetch.sh`) | verlangt, dass **jeder Nutzer** eine eigene Instagram-Session im Klartext ablegt. In einem Kundenprodukt ist das eine Compliance-Entscheidung, keine technische — gehoert nicht stillschweigend in eine Standard-Auslieferung |
| Cross-Repo-Sync / `#action:*`-Tags | setzt Patricks 5-Repo-Topologie voraus |
| Zeiterfassungs-Ledger | firmenspezifische Abrechnung |
| Git-Disziplin-Regeln | Cowork-spezifisch (Sandbox-Lock-Problematik), Hermes hat andere Voraussetzungen |

---

## Reihenfolge

```
Ticket 0 (Fundament)  →  Ticket 1 (Briefing)  →  Ticket 2 (Inbox)
                                                      ↓
                     Ticket 5 (Lint)  ←  Ticket 3+4 (Lifecycle, Weekly)
```

Ticket 0 zuerst, weil 1–4 auf der Ausgabe-Konvention und der Log-Entscheidung
aufsetzen. Ticket 1 als naechstes, weil dort der Abstand am groessten ist
(12 vs. 576 Zeilen) und das Ergebnis beim Kunden sofort sichtbar wird.

## Quellenlage

Die Brain-Fassungen liegen unter `~/Documents/Claude/patrick-brain/.claude/`
(`agents/`, `rules/`). Sie sind **Referenz, nicht Vorlage** — abschreiben erzeugt
personenbezogene Skills. Logik uebernehmen, Text neu schreiben.

Hintergrund zur Struktur:
`~/Documents/Claude/patrick-brain/_meta/agent-findings/2026-08-06-hermes-agent-topologie.md`
