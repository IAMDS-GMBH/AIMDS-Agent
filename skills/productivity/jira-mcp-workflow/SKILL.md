---
name: jira-mcp-workflow
description: LLM-optimized workflows and paginated query patterns for Atlassian, Jira, Tempo, time tracking, Zeiterfassung, and worklogs via AtlassianMCP. Use when querying, updating, logging work/time, tracking hours, or summarizing Jira issues, epics, sprints, worklogs, or Tempo hours in small blocks to prevent context overflow.
category: productivity
metadata:
  hermes:
    requires_tools:
      - jira_search
---

# Jira & Tempo MCP Workflow Strategy

Use this skill when interacting with Atlassian Jira issues, sprints, Tempo, time tracking (Zeiterfassung), or worklogs via AtlassianMCP (mcp-atlassian).

## Core Principle: Bounded Small-Block Queries
Never issue unbounded queries or request full issue dumps (`limit=50` or `fields="*all"`). Massive raw outputs (>100k chars) overflow LLM context windows and risk triggering server-side content/guardrail blocks (`llm-guard` PromptInjection). Always retrieve data in small, targeted blocks.

## Search Query Rules (`jira_search`)
- **Strict Bounds**: Always pass `limit` (10–15, max 20).
- **Required Fields**: Always pass explicit `fields` (e.g., `fields="key,summary,status,priority,assignee,updated"`).
- **Targeted JQL**:
  - Open user tickets: `project = <KEY> AND assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`
  - Completed user tickets: `project = <KEY> AND assignee = currentUser() AND statusCategory = Done ORDER BY updated DESC`
  - Epic issues: `parent = <EPIC_KEY> ORDER BY created ASC`
  - Recent activity: `project = <KEY> AND updated >= -7d ORDER BY updated DESC`

## Multi-Page Retrieval Pattern (Small Blocks)
1. Call `jira_search` with `limit: 10, start_at: 0` and selective `fields`.
2. Inspect `total` and returned count.
3. If more results are needed, fetch the next block with `limit: 10, start_at: 10`. Never request 50+ at once.

## Tempo & Zeiterfassung (Time Tracking & Worklogs)
- **Reading Worklogs**: Use `jira_get_worklog(issue_key="<KEY>")` to retrieve logged work for an issue, or `jira_search` with targeted JQL (e.g. `worklogAuthor = currentUser() AND worklogDate >= -7d`).
- **Logging Time (Zeiterfassung)**:
  - Call `jira_add_worklog`: `issue_key="<KEY>"`, `time_spent="1h 30m"`, `started` ISO string (e.g. `"2026-07-24T09:00:00.000+0000"`), and a markdown `comment`.
  - Always summarize worklog activity concisely in response text or store key summaries in Memory MCP via `memory_save`.

## Issue Details & Comments
- `jira_get_issue`: Pass selective `fields` (e.g., `fields="key,summary,status,priority,description,assignee"`).
- **Comments**: When reading comments, pass `include="comments"` with a tight `comment_limit` (e.g., `comment_limit=10`, max 20).

## Transitions
- Call `jira_get_transitions` to inspect available `transition_id` values.
- Call `jira_transition_issue` with the desired `transition_id`.
- Post internal notes with `jira_add_comment(public=false)` separately if required.

