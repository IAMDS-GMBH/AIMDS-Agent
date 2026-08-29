---
name: worklog-analytics
description: Deterministic capture, SQLite aggregation and structured evaluation of working time, Jira and Tempo worklogs, target hours (Sollzeit) and project time without LLM arithmetic errors; the report is saved to the Obsidian vault under reports/worklog/.
---

# Worklog & Project Time Analytics

## Purpose & Approach
This skill defines the binding standard for aggregating, analysing and documenting working time, worklogs (e.g. Jira/Confluence/M365) and project budgets.

**Core element:** LLMs must never do mental arithmetic or textual estimates for sums. All numbers are computed deterministically via SQLite.

---

## Standard Operating Procedure (SOP)

### Step 0: Working-time profile (region, weekly model)
- Target hours depend on country/federal state/canton, weekly hours, workdays per week and half days (24 and 31 December). The profile lives in memory as `Arbeitszeit-Profil`; `workdays(action='profile')` shows it.
- If `workdays` answers `worktime profile unknown`: **first clarify via `clarify` using the supplied choices** (Bayern / Baden-Württemberg / other German federal state / Austria / Switzerland; weekly hours; 5- or 6-day week), then `workdays(action='configure', region=…, weekly_hours=…, days_per_week=…, half_days=[…])` — this saves to memory. Never assume BW/DE or 40 h / 5 days.

### Step 1: Fetch raw data
- Fetch the raw data via the appropriate MCP tools (e.g. `atlassian-jira_get_worklog`, Jira search or time-tracking data).
- Avoid collapsing or guessing entries.

### Step 2: Deterministic SQLite ingestion
- Ingest the extracted records into the local SQLite database `~/.hermes/state.db` (table `mcp_records` or a temporary table `temp_worklogs`).
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

### Step 3: Mathematical aggregation via SQL
- Run all sums, averages, project breakdowns and rounding exclusively via SQL queries:
```sql
-- Grand total & conversion to hours
SELECT 
    COUNT(*) as total_entries,
    SUM(time_spent_seconds) as total_seconds,
    ROUND(SUM(time_spent_seconds) / 3600.0, 2) as total_hours,
    ROUND(SUM(time_spent_seconds) / (3600.0 * (SELECT MAX(weekly_hours * 1.0 / days_per_week) FROM workday_calendar)), 2) as total_person_days  -- hours per day from the profile, not a hardcoded 8
FROM temp_worklogs;

-- Group by author & ticket
SELECT 
    author,
    issue_key,
    ROUND(SUM(time_spent_seconds) / 3600.0, 2) as hours_spent
FROM temp_worklogs
GROUP BY author, issue_key
ORDER BY hours_spent DESC;
```

### Step 3b: Target hours deterministically (no typed calendars)
- **Never** write workdays, public holidays or target hours as literals in SQL or comments (no "Jan: 21 Mon–Fri", no Easter from memory). The source is `workdays`:
```text
workdays(action='materialize', start='2026-01-01', end='2026-08-31')
→ table workday_calendar (one row per day: factor 1/0.5/0, target_hours, holiday_name)
```
- Actual vs. target via JOIN — **aggregate worklogs per day first**, otherwise the target hours multiply:
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
- Vacation bookings (central ticket, e.g. `IAMDS-595`): 0.5 h booked = half a day, 1 h = a full day of target-hours deduction; hours per day = `weekly_hours / days_per_week` from the table, not hardcoded. Public holidays on weekends deduct nothing; weekend worklogs count towards actual, not towards target.

### Step 4: Executive Verification Gate (plausibility check)
- **Sum consistency:** Before output, check that `sum(group subtotals) == grand total` — also for target hours: the monthly rows (workdays, public holidays, target hours) must add up to the total row; a month "corrected" by hand is an error, not a fix.
- **Completeness:** Does the number of aggregated rows match the number of queried Jira tickets/worklogs?
- **Plausibility:** No negative hours, no unexplained outliers (>24 h per day per person).

### Step 5: SQLite cleanup
- Drop temporary intermediate tables immediately after the evaluation:
```sql
DROP TABLE IF EXISTS temp_worklogs;
```

### Step 6: Structured output & vault synchronisation
- Present the result to the user as a clean Markdown table.
- Save the report from `_templates/report.md` to `reports/worklog/<topic>-<year>.md` (e.g. `reports/worklog/arbeitszeit-2026.md`); overwrite the same file on every rerun and bump `updated:`.
- Link it from the topic hub in `projects/` via `related_to`.
- Never write to `journal/`, never create `_v2`/`FINAL` copies.
- Frontmatter per `_conventions.md`: `type: report`, `title`, `created`, `updated`, `status`, `covers`, `source`, `related_to`, `tags`.
- No emoji in headings; only months with data, no forecasts.

---

## Strict Guardrails
1. **No emergency Python scripts:** Never write ad-hoc Python scripts to `/tmp/` for simple additions or counts.
2. **No unrequested Excel files:** Do not generate `.xlsx` files via Office tools unless the user explicitly asks for an Excel export.
3. **No LLM mental arithmetic:** Never add 5+ numbers in free text. Always use `sql`.
4. **No typed calendars:** Weekdays per month, public holidays, Easter and target hours come from `workdays` (table `workday_calendar`), never from memory or SQL literals.
5. **Never guess the region:** Without an `Arbeitszeit-Profil`, run `clarify` first, then `workdays(action='configure')`.
