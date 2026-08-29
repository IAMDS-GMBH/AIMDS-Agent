---
name: worklog-analytics
description: Deterministische Erfassung, SQLite-Aggregation und strukturierte Auswertung von Arbeitszeiten, Jira Worklogs und Projektzeiten ohne LLM-Rechenfehler.
---

# Worklog & Project Time Analytics

## Zweck & Arbeitsweise
Dieser Skill definiert den verbindlichen Standard für die Aggregation, Analyse und Dokumentation von Arbeitszeiten, Worklogs (z. B. Jira/Confluence/M365) und Projektbudgets. 

**Kernelement:** LLMs dürfen niemals manuelle Kopfrechnung oder Textschätzungen für Summen durchführen. Alle Zahlen werden deterministisch über SQLite berechnet.

---

## Standard Operating Procedure (SOP)

### Schritt 0: Arbeitszeit-Profil (Region, Wochenmodell)
- Sollzeit hängt von Land/Bundesland/Kanton, Wochenstunden, Arbeitstagen pro Woche und Halbtagen (24./31.12.) ab. Das Profil liegt im Memory als `Arbeitszeit-Profil`; `workdays(action='profile')` zeigt es.
- Antwortet `workdays` mit `worktime profile unknown`: **erst per `clarify` mit den gelieferten Choices klären** (Bayern / Baden-Württemberg / anderes Bundesland / Österreich / Schweiz; Wochenstunden; 5- oder 6-Tage-Woche), dann `workdays(action='configure', region=…, weekly_hours=…, days_per_week=…, half_days=[…])` — das speichert im Memory. Niemals BW/DE oder 40h/5 Tage annehmen.

### Schritt 1: Rohdaten abrufen
- Rufe die Rohdaten über die entsprechenden MCP-Tools ab (z. B. `atlassian-jira_get_worklog`, Jira-Suche oder Zeiterfassungsdaten).
- Vermeide das Zusammenstauchen oder Raten von Einträgen.

### Schritt 2: Deterministische SQLite-Ingestion
- Ingestiere die extrahierten Datensätze in die lokale SQLite-Datenbank `~/.hermes/state.db` (Tabelle `mcp_records` oder eine temporäre Tabelle `temp_worklogs`).
```sql
CREATE TEMP TABLE IF NOT EXISTS temp_worklogs (
    id TEXT PRIMARY KEY,
    issue_key TEXT,
    author TEXT,
    time_spent_seconds INTEGER,
    started_at TEXT,
    comment TEXT
);
```

### Schritt 3: Mathematische Aggregation via SQL
- Führe alle Summen, Durchschnitte, Projekt-Breakdowns und Rundungen ausschließlich per SQL-Query aus:
```sql
-- Gesamtsumme & Stunden-Umrechnung
SELECT 
    COUNT(*) as total_entries,
    SUM(time_spent_seconds) as total_seconds,
    ROUND(SUM(time_spent_seconds) / 3600.0, 2) as total_hours,
    ROUND(SUM(time_spent_seconds) / (3600.0 * (SELECT MAX(weekly_hours * 1.0 / days_per_week) FROM workday_calendar)), 2) as total_person_days  -- Stunden/Tag aus dem Profil, nicht 8 hartkodiert
FROM temp_worklogs;

-- Gruppierung nach Bearbeiter & Ticket
SELECT 
    author,
    issue_key,
    ROUND(SUM(time_spent_seconds) / 3600.0, 2) as hours_spent
FROM temp_worklogs
GROUP BY author, issue_key
ORDER BY hours_spent DESC;
```

### Schritt 3b: Sollzeit deterministisch (kein Kalender-Tippen)
- Arbeitstage, Feiertage und Sollstunden **nie** als Literale in SQL oder Kommentare schreiben (kein „Jan: 21 Mo–Fr", kein Ostern aus dem Kopf). Quelle ist `workdays`:
```text
workdays(action='materialize', start='2026-01-01', end='2026-08-31')
→ Tabelle workday_calendar (eine Zeile je Tag: factor 1/0.5/0, target_hours, holiday_name)
```
- Ist/Soll per JOIN — Worklogs **erst je Tag aggregieren**, sonst vervielfachen sich die Sollstunden:
```sql
WITH ist AS (
  SELECT substr(timestamp,1,10) AS day, SUM(duration_seconds)/3600.0 AS hours
  FROM mcp_records WHERE tool_name = 'mcp_TempoMCP_retrieveWorklogs' AND reference_key != 'IAMDS-595' GROUP BY 1),
urlaub AS (
  SELECT substr(timestamp,1,7) AS month,
         SUM(CASE WHEN ROUND(duration_seconds/3600.0,1) = 0.5 THEN 0.5 WHEN ROUND(duration_seconds/3600.0,1) = 1.0 THEN 1.0 ELSE 0 END) AS days
  FROM mcp_records WHERE reference_key = 'IAMDS-595' GROUP BY 1)
SELECT c.month,
       ROUND(SUM(c.target_hours),2)                              AS soll_brutto,
       ROUND(COALESCE(MAX(u.days),0) * MAX(c.weekly_hours)/MAX(c.days_per_week),2) AS urlaub_h,
       ROUND(SUM(c.target_hours) - COALESCE(MAX(u.days),0) * MAX(c.weekly_hours)/MAX(c.days_per_week),2) AS soll_netto,
       ROUND(COALESCE(SUM(i.hours),0),2)                         AS ist,
       ROUND(COALESCE(SUM(i.hours),0) - (SUM(c.target_hours) - COALESCE(MAX(u.days),0) * MAX(c.weekly_hours)/MAX(c.days_per_week)),2) AS saldo
FROM workday_calendar c
LEFT JOIN ist i ON i.day = c.day
LEFT JOIN urlaub u ON u.month = c.month
WHERE c.day BETWEEN '2026-01-01' AND '2026-08-31'
GROUP BY c.month ORDER BY c.month;
```
- Urlaubsbuchungen (zentrales Ticket, z. B. `IAMDS-595`): 0,5h gebucht = halber Tag, 1h = ganzer Tag Sollzeit-Abzug; Stunden pro Tag = `weekly_hours / days_per_week` aus der Tabelle, nicht hartkodiert. Feiertage am Wochenende ziehen nichts ab; Wochenend-Worklogs zählen in Ist, nicht in Soll.

### Schritt 4: Executive Verification Gate (Plausibilitätsprüfung)
- **Summenkonsistenz:** Prüfe vor der Ausgabe, ob `Summe(Gruppen-Teilsummen) == Gesamtsumme` — auch für Sollzeit: die Monatszeilen (Arbeitstage, Feiertage, Sollstunden) müssen die Gesamtzeile ergeben; ein per Hand „korrigierter" Monat ist ein Fehler, kein Fix.
- **Vollständigkeit:** Stimmt die Anzahl der aggregierten Zeilen mit der Anzahl der abgefragten Jira-Tickets/Worklogs überein?
- **Plausibilität:** Keine negativen Stunden, keine unbegründeten Ausreißer (>24h pro Tag pro Person).

### Schritt 5: SQLite Cleanup
- Bereinige temporäre Zwischentabellen unmittelbar nach der Auswertung:
```sql
DROP TABLE IF EXISTS temp_worklogs;
```

### Schritt 6: Strukturierte Ausgabe & Vault-Synchronisation
- Präsentiere das Ergebnis in einer sauberen Markdown-Tabelle für den Nutzer.
- Aktualisiere bei Bedarf den kanonischen Projekthub im Obsidian Vault (`~/Documents/AIMDS-Suite-Vault/projects/<Projekt>/<Projekt>.md`) im Abschnitt `## Zeiterfassung & Budget`.

---

## Strikte Guardrails
1. **Keine Python-Notfallskripte:** Schreibe niemals Ad-hoc Python-Skripte nach `/tmp/` für simple Additionen oder Zählungen.
2. **Keine unaufgeforderten Excel-Dateien:** Generiere keine `.xlsx`-Dateien über Office-Tools, es sei denn, der Nutzer verlangt explizit einen Excel-Export.
3. **Keine LLM-Kopfrechnung:** Führe niemals Additionen von 5+ Zahlen im Freitext aus. Nutze immer `sql`.
4. **Keine getippten Kalender:** Wochentage je Monat, Feiertage, Ostern, Sollstunden kommen aus `workdays` (Tabelle `workday_calendar`), nie aus dem Gedächtnis oder aus SQL-Literalen.
5. **Region nie raten:** Ohne `Arbeitszeit-Profil` erst `clarify`, dann `workdays(action='configure')`.
