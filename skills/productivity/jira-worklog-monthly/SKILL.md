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
- **Jira Standard API (`jira_get_worklog`)**: Jira stores Tempo-booked entries with a generic `"time-tracking"` comment string.
- **TempoMCP (`get_worklogs`) / Tempo REST API (`GET https://api.tempo.io/4/worklogs?from=YYYY-MM-DD&to=YYYY-MM-DD`)**: Returns the **actual user descriptions and activity comments** (e.g. `"ECO-797: LZ Provisioner: Improve DevOps Deployment Step - Idempotenz & Self-Healing"`), because Tempo stores detailed attributes in Tempo Timesheets.
- **Rule**: When the user asks for daily worklog comments or activity logs, use `TempoMCP` (`get_worklogs`) or Tempo API instead of Jira's standard `jira_get_worklog`.

### 3. Reading Locked/Closed Periods (Gesperrte Zeiträume)
Tempo period locks (Timesheet Period Locks) only block new bookings or edits (`create_worklog`/`update_worklog`). **Read operations (`get_worklogs`) on locked, closed, or archived periods remain fully supported.** Never refuse to query worklogs for past or locked months.

## Paginated Fetch & Filter Workflow

### 1. Paginated Worklog Retrieval Pattern
When retrieving worklogs for a heavy issue:
1. Retrieve worklog entries using `jira_get_worklog(issue_key="<KEY>")` or Tempo MCP `get_worklogs(issue_key="<KEY>")`.
2. If `jira_get_worklog` returns a truncated set or does not accept month parameters directly, query Jira issue worklogs with `include="worklogs"` or execute a REST helper loop.

### 2. SQLite Ingestion & SQL Querying
Instead of writing in-prompt Python string-parsing or array-filtering loops, always ingest the retrieved worklog dataset into local SQLite (`~/.hermes/state.db` table `mcp_records` or `external_worklogs`) as described in the `sql-tabular-processor` skill.

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
