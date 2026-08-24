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
    ROUND(SUM(time_spent_seconds) / (3600.0 * 8.0), 2) as total_person_days
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

### Schritt 4: Executive Verification Gate (Plausibilitätsprüfung)
- **Summenkonsistenz:** Prüfe vor der Ausgabe, ob `Summe(Gruppen-Teilsummen) == Gesamtsumme`.
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
