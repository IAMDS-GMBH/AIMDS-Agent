---
name: jira-worklog-monthly
description: Fetch, paginate, filter, and summarize Jira worklogs, Tempo time bookings, and daily comments for an issue (e.g., EXT-95) by month (YYYY-MM). Handles large worklog sets (>1,000 entries) without truncation or context overflow.
category: productivity
metadata:
  hermes:
    requires_tools:
      - worklog
      - sql
---

# Jira & Tempo Monthly Worklog & Comment Pagination Strategy

Use this skill when fetching or auditing worklogs, time bookings (Zeiterfassung), and daily comments for Jira issues with large worklog histories (e.g. `EXT-95` with >1,000 entries) or when filtering time bookings by month (`YYYY-MM`).

## Core Rules & Strategy

### 1. Mandatory Time-Range Filtering
Worklog queries must ALWAYS be restricted by time range (e.g., month `YYYY-MM`, `from`/`to` dates, or `worklogDate >= -30d`) BEFORE fetching. Never run unbounded worklog queries on long-running issues.

### 2. Tempo Comments vs. Jira Worklog Marker
- **Jira Standard API (`jira_get_worklog`)**: Takes ONLY `issue_key` — it has NO `from`/`to`/date or user filter parameter at all, and internally auto-paginates through the issue's ENTIRE worklog history regardless of size. For shared/long-running issues (e.g. a company-wide vacation-tracking ticket) this always returns every employee's entries across every year in one call. Jira also stores Tempo-booked entries with a generic `"time-tracking"` comment string, and the `author` field is frequently the **Tempo sync/integration bot account** (e.g. `"Timesheets by Tempo - Jira Time Tracking"`), NOT the real person — so filtering the ingested result by `author`/`user_id` is unreliable for this tool. Only filter it by `started`/date after the full fetch.
- **TempoMCP (`retrieveWorklogs`/`get_worklogs`) / Tempo REST API (`GET https://api.tempo.io/4/worklogs?from=YYYY-MM-DD&to=YYYY-MM-DD`)**: Accepts real `startDate`/`endDate` (required) and optional `users`/`program`/`team` filters (defaults to the token owner's own worklogs), returns the **actual user descriptions and activity comments** (e.g. `"ECO-797: LZ Provisioner: Improve DevOps Deployment Step - Idempotenz & Self-Healing"`) with correct per-user attribution, because Tempo stores detailed attributes and real author accountIds in Tempo Timesheets.
- **Rule**: ALWAYS prefer `TempoMCP` (`retrieveWorklogs`/`get_worklogs`/`getWorklogAnalytics`) over Jira's `jira_get_worklog` for worklog/timesheet/vacation queries whenever TempoMCP is configured — it gives you real date-range and user filtering plus correct authorship. Only use `jira_get_worklog` as a fallback for issues you already know have few worklog entries, or when TempoMCP isn't configured.

### 3. Reading Locked/Closed Periods (Gesperrte Zeiträume)
Tempo period locks (Timesheet Period Locks) only block new bookings or edits (`create_worklog`/`update_worklog`). **Read operations (`get_worklogs`) on locked, closed, or archived periods remain fully supported.** Never refuse to query worklogs for past or locked months.

## Paginated Fetch & Filter Workflow

### 1. Paginated Worklog Retrieval Pattern
When retrieving worklogs for a heavy issue:
1. If TempoMCP is configured, ALWAYS prefer `retrieveWorklogs`/`get_worklogs(startDate=..., endDate=..., users=[...])` — it supports real date-range and user filtering server-side.
2. Only if TempoMCP is unavailable, fall back to `jira_get_worklog(issue_key="<KEY>")`. Be aware this tool takes NO date/user parameter and always returns the issue's FULL worklog history — filter the result afterwards by `started` date only (its `author` field is often the Tempo sync bot, not the real person, so do not filter by author/user).

### 2. SQLite Ingestion & SQL Querying
Instead of writing in-prompt Python string-parsing or array-filtering loops, always ingest the retrieved worklog dataset into local SQLite (`~/.hermes/state.db` table `mcp_records` or `external_worklogs`) as described in the `sql-tabular-processor` skill.

Freshness: `mcp_records` mirrors past fetches — a fetch replaces only its requested date window. When the user reports changed bookings, re-fetch the affected range (include those dates in `startDate`/`endDate`) before recomputing; for sums always filter `duration_seconds > 0` (container rows carry 0 duration). Stale-range repair: `DELETE FROM mcp_records WHERE tool_name='...' AND substr(timestamp,1,10) BETWEEN '...' AND '...'`, then re-fetch.

Perform calculations (monthly totals, leave balance deductions, daily timelines) using standard SQL queries:
```sql
SELECT 
    strftime('%Y-%m', started) AS month,
    issue_key,
    author,
    ROUND(SUM(time_spent_seconds) / 3600.0, 2) AS total_hours,
    COUNT(*) AS entry_count
FROM external_worklogs
WHERE started LIKE '2026-07%'
GROUP BY month, issue_key, author;
```

### 3. Extracting Comments & Daily Activities
Extract key fields from each worklog entry for monthly reports:
- `started`: Date & time of work entry
- `timeSpent` / `timeSpentSeconds`: Duration logged
- `author`: Display name or email
- `comment`: Daily activity description (e.g. "Team Azure Daily", "ECO-838: LZ Provisioner")

### 4. Summary & Memory MCP Preservation
When summarizing monthly worklogs for time reporting or billing:
- Sum up total hours for the selected month.
- Group entries by activity category or sub-task tags (e.g. `ECO-xxx`).
- Save key monthly summaries in Memory MCP (`memory_save`) so future sessions can reference past monthly totals instantly without re-fetching 1,000+ entries.
