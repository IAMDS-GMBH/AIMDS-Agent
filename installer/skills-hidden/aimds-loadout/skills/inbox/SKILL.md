---
name: inbox
description: Verarbeitet eingehende Diktate/Nachrichten zuverlässig als Inbox-Workflow — klassifizieren, gegen die Quell-URL deduplizieren, bestehenden Eintrag erweitern oder neu anlegen, verlinken und klar bestätigen. Gehärtet gegen Parallelläufe, Doppelanlage und stille Fehler.
---

# Inbox Workflow (Diktate & Nachrichten)

## Ziel
Eingehende Diktate/Nachrichten strukturiert und **reproduzierbar** in den Workspace
überführen — ohne stille Fehler, ohne doppelte Einträge, ohne dass ein paralleler Lauf
denselben Input zweimal ablegt.

## Phase 0 — Lauf-Lock (allererste Aktion)
Zwei parallele Läufe können dieselbe Inbox-Datei verarbeiten und zwei Einträge für
denselben Inhalt anlegen. Deshalb zuerst ein Lock, bevor irgendetwas anderes passiert.

Lock-Datei: `_inbox/.inbox-lock` (transiente Laufzeit-Koordination, kein Run-Log).

1. Existiert die Lock-Datei?
   - **Nein** → Lock schreiben (eine Zeile: `<ISO-Zeitstempel> · <auslöser>`, Auslöser
     = `scheduled` | `briefing` | `interaktiv`), weiter.
   - **Ja, Zeitstempel jünger als 30 Min** → **Lauf abbrechen.** Nichts anfassen, kein
     Archivieren, keine Ausgabe außer einer Zeile "übersprungen — anderer Lauf aktiv".
   - **Ja, Zeitstempel älter als 30 Min** → als verwaist behandeln (abgestürzter Vorlauf),
     überschreiben, im Run-Report `⚠ verwaisten Lock überschrieben` vermerken.
2. **Am Run-Ende (Pflicht, auch im Fehlerfall):** Lock-Datei löschen. `rm` der eigenen
   Lock-Datei ist hier ausdrücklich erlaubt.

> 30 Min ist länger als der längste realistische Lauf, aber kurz genug, dass ein Absturz
> den nächsten Lauf nicht bis zum Folgetag blockiert. Der Lock verhindert
> **Gleichzeitigkeit** — gegen Doppelanlage nach Teilabbruch greift der URL-Check unten.

## Phase 1 — Verarbeiten (verpflichtende Reihenfolge je Item)

### 1. Idempotenz-Marker setzen (vor der Verarbeitung)
- Steht bereits `processing_started:` im Frontmatter, die Datei ist aber **nicht**
  archiviert? Dann ist ein vorheriger Lauf abgebrochen — vorsichtig prüfen, was schon
  angelegt wurde, statt blind neu zu erzeugen.
- Sonst **sofort** `processing_started: YYYY-MM-DDTHH:mm` ins Frontmatter schreiben,
  bevor irgendetwas verarbeitet wird.

### 2. Klassifizieren
Typ, Thema, Priorität und gewünschte Aktion bestimmen. Routing-Ziel **immer** aus der
Routing-Tabelle in `AGENTS.md` des aktiven Workspace lesen — keine fest codierten
Route-Mappings im Skilltext oder in Tool-Argumenten.

### 3. Bestehend prüfen — Duplikat-Check gegen die Quell-URL (nicht gegen den Dateinamen)
Ein Dateinamen-Vergleich reicht nicht: derselbe Inhalt bekommt von zwei Läufen zwei
verschiedene, jeweils plausible Slugs — und landet doppelt. Trägt das Item eine `url:`
im Frontmatter:
1. URL auf ihre stabile Kennung normalisieren (Query-Parameter, Trailing-Slash und
   `www.` ignorieren; bei Plattform-Links den Shortcode/Post-Identifier nehmen).
2. Per Grep über `knowledge/`, `ideas/`, `projects/`, `contacts/`, `notes/`,
   `decisions/` nach dieser Kennung suchen.
3. **Treffer → nicht neu anlegen**, sondern die bestehende Datei erweitern und
   `updated:` setzen.
4. Kein Treffer → neu anlegen. Die Quell-URL **muss** dabei ins Frontmatter, sonst
   greift der Check beim nächsten Lauf nicht.

### 4. Erweitern oder neu anlegen
Duplikat/Fortsetzung erweitern; sonst neu erstellen. Bei Projektnotizen **vor** dem
Schreiben `projectStatus` des Ziel-Projekts prüfen (siehe
`guardrails/project-lifecycle.md`): `active`/`waiting` → anhängen erlaubt;
`dormant`/`done`/`parked` → STOPP, stattdessen als Notiz/Knowledge-Eintrag mit
`related_to: [[projekt]]`-Backlink ablegen und im Report vermerken.

### 5. Kern-Insight sicherstellen (Gate vor dem Archivieren)
Ein Item darf **erst archiviert werden, wenn das Thema extrahiert und abgelegt ist.**
Prüfung vor jeder Archivierung:
1. Wurde eine Ziel-Datei angelegt **oder** eine bestehende sinnvoll ergänzt?
2. Steht darin ein klar formuliertes Kern-Insight (1–3 Sätze: was ist das Thema, warum
   relevant)?

Nein auf 1 oder 2 → **nicht archivieren.** Besonders bei CTA-/Funnel-Inhalten ist die
sichtbare Botschaft ("schreib mir X", "Link in Bio") nicht das Thema — das Thema ist
das, worüber der Inhalt inhaltlich spricht. Erst extrahieren, dann archivieren.

### 6. Auto-Linking
Mindestens einen relevanten bestehenden `[[wikilink]]` ergänzen, falls Kandidaten
existieren. Existiert keiner: das explizit als Ergebnis nennen.

### 7. Archivieren (nie löschen)
Nach **erfolgreicher** Verarbeitung: `verarbeitet: YYYY-MM-DD` + Ziel-Pfad ins
Frontmatter, dann nach `_inbox/_archive/` verschieben. Nie löschen (siehe
`_conventions.md`). Der `verarbeitet:`-Marker ist zugleich das Signal, an dem das
Morgen-Briefing die Inbox-Frische erkennt.

### 8. Bestätigen
Dem Nutzer knapp und eindeutig melden, **was wo** abgelegt/erweitert wurde und welche
Links gesetzt wurden. Kein stiller Abschluss.

## Fehlerpfade — explizit behandeln
- **Klassifikation unklar** → gezielte Rückfrage statt raten; alternativ mit
  `needs-triage` in `_inbox/` parken.
- **Verarbeitung schlägt fehl** → `error: <Beschreibung>` + `error_date: YYYY-MM-DD` ins
  Frontmatter, `attempts:` um 1 erhöhen, Datei in `_inbox/` lassen, zum nächsten Item.
  **Kein stiller Abbruch**, keine Erfolgsmeldung.
- **Attempt-Tracking:** Ab `attempts: 3` nicht weiter endlos retrien — den Rohinhalt als
  Notiz mit `status: raw` ablegen (Ziel per Routing-Tabelle), das Item archivieren und
  im Report als "nach 3 Versuchen als Rohnotiz abgelegt" ausweisen.
- **Kein Link-Kandidat vorhanden** → explizit als Ergebnis nennen.

## Verifikation (Selbstprüfung vor dem Abschluss)
- [ ] Lauf-Lock gesetzt und am Ende wieder gelöscht?
- [ ] Duplikat-Check gegen die `url:` gelaufen (nicht nur Dateiname)?
- [ ] Bei Duplikat die bestehende Datei erweitert (kein doppelter Neueintrag)?
- [ ] Jedes archivierte Item hat ein Kern-Insight in der Ziel-Datei?
- [ ] Mindestens ein relevanter Link gesetzt, sofern Kandidaten vorhanden waren?
- [ ] Fehlgeschlagene Items mit `error:`/`attempts:` markiert, nicht still verschluckt?
- [ ] Knappe, eindeutige Abschlussbestätigung an den Nutzer?

## Ausgabe-Format
Abschlussbestätigung und Run-Report folgen `guardrails/output-format.md` (§5 Run-Report:
Verarbeitet-Anzahl, Status, Aktionen). Bei leerer Inbox kurz "Inbox leer, nichts zu tun".

## Was NICHT
- Keine stillen Fallbacks ohne Rückmeldung.
- Keine erfundenen Quellen/Links.
- Kein Löschen — immer archivieren.
- Kein Versand externer Nachrichten ohne explizite Freigabe.
