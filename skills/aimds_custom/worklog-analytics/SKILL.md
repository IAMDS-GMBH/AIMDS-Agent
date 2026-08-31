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

### Step 0: Working-time profile (region, weekly model, source patterns)
- Target hours depend on country/federal state/canton, weekly hours, working days (5/6-day weeks or explicit `work_weekdays` like Mo–We), full/part time and half days (24 and 31 December). The profile lives in memory as `Arbeitszeit-Profil`; `workdays(action='profile')` shows it. It also carries the genericity keys: `worklog_source_tool` (LIKE pattern matching the user's time bookings in `mcp_records.tool_name` — works for any worklog MCP, not just Tempo/Jira) and optionally `vacation_booking_patterns` + `vacation_hour_factor` for the booking-ticket workaround.
- If `workdays` answers `worktime profile unknown`: **first try `workdays(action='estimate_profile')`** — it proposes a week model and source tool from already-ingested worklog data. Present the proposal and confirm via `clarify` (region is never estimated; also confirm weekly hours and full/part time), then `workdays(action='configure', region=…, weekly_hours=…, days_per_week=… or work_weekdays=[…], half_days=[…], worklog_source_tool=…, …)` — this saves to memory. Never assume BW/DE or 40 h / 5 days.

### Step 0b: Preferred path — one-call report
- For actual-vs-target questions call `workdays(action='report', start=…, end=…)` first: it materializes the calendar, aggregates actuals from `mcp_records` via the profile's `worklog_source_tool`, credits vacation from the `absences` table, and computes delta — all in SQLite, clamped to today (a full-year request additionally returns `target_full_range`). Follow its `hints` when data is missing or stale. Only fall back to the manual JOIN below for custom breakdowns.

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
- Actual vs. target via JOIN — **aggregate worklogs per day first**, otherwise the target hours multiply. `<worklog pattern>` and `<vacation pattern>` come from the profile (`worklog_source_tool`, `vacation_booking_patterns`), never hardcoded tool or ticket names:
```sql
WITH ist AS (
  SELECT substr(timestamp,1,10) AS day, SUM(duration_seconds)/3600.0 AS hours
  FROM mcp_records WHERE tool_name LIKE '<worklog pattern>' AND reference_key NOT LIKE '<vacation pattern>' GROUP BY 1),
urlaub AS (
  SELECT c.month AS month, SUM(a.portion * c.target_hours) AS hours
  FROM absences a JOIN workday_calendar c ON c.day = a.day
  WHERE a.kind = 'vacation' GROUP BY 1)
SELECT c.month,
       ROUND(SUM(c.target_hours),2)                               AS soll_brutto,
       ROUND(COALESCE(MAX(u.hours),0),2)                          AS urlaub_h,
       ROUND(SUM(c.target_hours) - COALESCE(MAX(u.hours),0),2)    AS soll_netto,
       ROUND(COALESCE(SUM(i.hours),0),2)                          AS ist,
       ROUND(COALESCE(SUM(i.hours),0) - (SUM(c.target_hours) - COALESCE(MAX(u.hours),0)),2) AS saldo
FROM workday_calendar c
LEFT JOIN ist i ON i.day = c.day
LEFT JOIN urlaub u ON u.month = c.month
WHERE c.day BETWEEN '2026-01-01' AND '2026-08-31'
GROUP BY c.month ORDER BY c.month;
```
- Public holidays on weekends deduct nothing; weekend worklogs count towards actual, not towards target.

### Step 3c: Vacation sources (the `absences` table)
Vacation credit always comes from the source-neutral `absences` table (day, portion, kind, source) — a booking ticket is only one way to fill it, and the ticket may change per year:
1. **Booking import (workaround):** `workdays(action='absences', op='import_from_bookings')` converts bookings matching the profile's `vacation_booking_patterns` into day portions via `vacation_hour_factor` (e.g. factor 8.0: 1 h booked = a full 8 h day, 0.5 h = half a day).
2. **Direct user input:** the user states their vacation ("3.–14. August") → `workdays(action='absences', op='add', days=[{'from': '2026-08-03', 'to': '2026-08-14'}])` — ranges expand to working days only.
3. **Vault note:** read the note, extract the days, then `op='add'` with `source='vault:<note>'`.
4. **Document (Excel/PDF):** ingest via the doc-ingest flow, pull the FULL markdown (never word-chunks — they cut table rows), parse the day-level dates (disambiguate DD.MM.YYYY), then `op='add'` with `source='document:<doc_id>'`.
If none of these is available, ask the user for a reference or the vacation days themselves — never guess.

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

### Step 3d: Freshness — `mcp_records` mirrors past fetches
- `mcp_records` is NOT live data: it holds whatever past fetches returned. A fetch is authoritative only for its **requested date window** — the ingestor replaces that window on re-fetch, but ranges outside the window keep their old rows.
- **When the user says bookings changed** (vacation moved, worklog corrected/deleted): re-fetch the affected range explicitly (include the changed dates in `startDate`/`endDate`), then recompute. Never re-run SQL over the old mirror and present it as current.
- Check data age: `report` coverage shows `last_fetched_at` per source; an old value means stale mirror even though the range looks covered.
- Repair for a range no fetch will cover again: `DELETE FROM mcp_records WHERE tool_name = '...' AND substr(timestamp,1,10) BETWEEN '...' AND '...'` via `sql`, then re-fetch.
- For sums/counts always filter `duration_seconds > 0` — flattened container rows carry 0 duration and inflate counts.
- After a re-fetch that touches vacation bookings, rerun `workdays(action='absences', op='import_from_bookings')` — it drops its previously derived rows in the window before re-importing, so cancelled days disappear from vacation credit.

## Strict Guardrails
1. **No emergency Python scripts:** Never write ad-hoc Python scripts to `/tmp/` for simple additions or counts.
2. **No unrequested Excel files:** Do not generate `.xlsx` files via Office tools unless the user explicitly asks for an Excel export.
3. **No LLM mental arithmetic:** Never add 5+ numbers in free text. Always use `sql`.
4. **No typed calendars:** Weekdays per month, public holidays, Easter and target hours come from `workdays` (table `workday_calendar`), never from memory or SQL literals.
5. **Never guess the region:** Without an `Arbeitszeit-Profil`, run `clarify` first, then `workdays(action='configure')`.
