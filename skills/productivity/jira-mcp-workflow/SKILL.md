---
name: jira-mcp-workflow
description: LLM-optimized workflows and query patterns for Jira Cloud & Data Center via AtlassianMCP (mcp-atlassian). Use when querying, updating, or summarizing Jira tickets, epics, sprints, or worklogs.
category: productivity
---

# Jira MCP Workflow Strategy

Use this skill when interacting with Jira issues, sprints, or worklogs via AtlassianMCP.

## Search Query Rules (`jira_search`)
- **Strict Bounds**: Always pass `limit` (10–15, max 20).
- **Required Fields**: Always pass `fields` (e.g., `fields="key,summary,status,priority,assignee,updated"`).
- **Targeted JQL**:
  - Open user tickets: `project = <KEY> AND assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`
  - Completed user tickets: `project = <KEY> AND assignee = currentUser() AND statusCategory = Done ORDER BY updated DESC`
  - Epic issues: `parent = <EPIC_KEY> ORDER BY created ASC`
  - Recent project activity: `project = <KEY> AND updated >= -7d ORDER BY updated DESC`

## Multi-Page Retrieval Pattern
1. Call `jira_search` with `limit: 10, start_at: 0`.
2. Inspect `total` and count returned issues.
3. If more needed, call `jira_search` with `limit: 10, start_at: 10`. Never request 50+ at once.

## Issue Detail & Transitions
- `jira_get_issue`: Pass `fields="key,summary,status,priority,description,assignee"` unless full history needed.
- `jira_transition_issue`:
  1. Call `jira_get_transitions` to get `transition_id`.
  2. Call `jira_transition_issue` with `transition_id`.
  3. Call `jira_add_comment(public=false)` separately for internal notes (do not embed comments inside transition payload).

## Worklog & SLA
- `jira_add_worklog`: `time_spent="1h 30m"`, `started` ISO string, `comment` markdown summary.
- `jira_get_issue_sla`: Use for lead/cycle time or time-in-status metrics.
