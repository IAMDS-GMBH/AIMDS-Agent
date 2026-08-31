---
name: sql-tabular-processor
description: Generic high-performance SQLite data processor for MCP tool exports, JSON dumps, CSVs, worklogs (Jira, Tempo, OpenProject), and support logs. Automatically ingests structured/tabular data into local SQLite for fast, 100% accurate SQL queries, aggregations, and reports without Python string-parsing or token overflow.
category: productivity
---

# Generic SQLite Tabular Data Processor

Use this skill whenever processing large MCP tool outputs, JSON arrays, CSV/TSV exports, time tracking entries (Jira, Tempo, OpenProject, Redmine), log dumps, or multi-row API results.

## Core Principle: SQL Over Python Parsing

Never write ad-hoc Python string-parsing scripts, regexes, or in-prompt list comprehensions to filter, sum, or transform tabular/MCP data. Always load the structured dataset into a local SQLite database (`~/.hermes/state.db` or `~/.hermes/data_cache.db`) and query it using standard SQL.

## Standard Ingestion Pattern

### 1. Import JSON / Tabular Data into SQLite
Load JSON arrays or CSV files directly into a local SQLite table. 

Using python's built-in `sqlite3` and `json` modules (or `sqlite3` CLI with `json_each`):

```python
import json, sqlite3

# Load JSON payload (from file or MCP response)
with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

conn = sqlite3.connect("~/.hermes/state.db")  # or data_cache.db
cursor = conn.cursor()

# Ensure table schema exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS mcp_records (
    id TEXT PRIMARY KEY,
    source TEXT,
    reference_key TEXT,
    timestamp DATETIME,
    user_id TEXT,
    duration_seconds INTEGER,
    category TEXT,
    comment TEXT,
    raw_data JSON
)
""")

# Batch insert
records = []
for item in (data if isinstance(data, list) else data.get("worklogs", [])):
    records.append((
        item.get("id"),
        item.get("source", "mcp"),
        item.get("issueKey") or item.get("issue", {}).get("key"),
        item.get("started") or item.get("created_at"),
        item.get("author", {}).get("displayName") or item.get("user"),
        item.get("timeSpentSeconds", 0),
        item.get("category", "work"),
        item.get("comment", ""),
        json.dumps(item)
    ))

cursor.executemany("""
INSERT OR REPLACE INTO mcp_records 
(id, source, reference_key, timestamp, user_id, duration_seconds, category, comment, raw_data)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", records)

conn.commit()
conn.close()
```

### 2. Fast SQL Analytics Queries

Once ingested, answer user requests directly with standard SQL queries using the built-in `sql` tool:

#### Monthly Worklog & Time Summary
```sql
SELECT 
    strftime('%Y-%m', timestamp) AS month,
    reference_key,
    user_id,
    ROUND(SUM(duration_seconds) / 3600.0, 2) AS total_hours,
    COUNT(*) AS entry_count
FROM mcp_records
WHERE timestamp >= '2026-01-01'
GROUP BY month, reference_key, user_id
ORDER BY month DESC;
```

#### Leave / Vacation Calculation
Vacation days live in the source-neutral `absences` table (filled via `workdays(action='absences')` — booking import with the profile's `vacation_booking_patterns`, direct user input, vault notes, or documents). Never hardcode a vacation ticket key:
```sql
SELECT
    strftime('%Y-%m', day) AS month,
    ROUND(SUM(portion), 2) AS days_taken,
    30.0 - SUM(SUM(portion)) OVER (ORDER BY strftime('%Y-%m', day)) AS remaining_leave_balance
FROM absences
WHERE kind = 'vacation'
GROUP BY month;
```

#### Daily Activity Timeline
```sql
SELECT 
    date(timestamp) AS work_date,
    GROUP_CONCAT(reference_key || ': ' || comment, ' | ') AS daily_activities,
    ROUND(SUM(duration_seconds) / 3600.0, 2) AS total_hours
FROM mcp_records
GROUP BY work_date
ORDER BY work_date DESC;
```

## Supported Source Platforms
- **Jira & Tempo Timesheets**: `jira_get_worklog`, Tempo `get_worklogs`
- **OpenProject**: Time entries API exports
- **Redmine / Azure DevOps**: Time tracking & issue exports
- **Support Cases**: Support ticket logs & manifest dumps
- **CSV / Excel / TSV**: File conversion exports
