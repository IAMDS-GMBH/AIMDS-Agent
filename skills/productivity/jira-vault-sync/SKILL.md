---
name: jira-vault-sync
description: Syncs Jira tickets, epics, and assigned issue deltas into the local Obsidian Vault. Supports project ticket ingestion, epic hierarchy grouping, and assigned ticket update tracking. Trigger when user asks "sync Jira tickets", "get Jira epics", "check my Jira changes", or "Jira vault sync".
---

# Jira Vault Sync & Delta Tracking Skill

This skill retrieves Jira tickets, Epics, and assigned issue updates, formatting them cleanly and persisting them into the local Obsidian Vault (`notes/jira/` or `projects/`).

## Workflows

### 1. Ingest Project Tickets to Vault
- Query tickets via Jira tools using bounded JQL (e.g. `project = [PROJECT] ORDER BY updated DESC`) with `limit=20` and explicit fields (`key,summary,status,assignee,priority,parent,updated`).
- Create or update markdown notes in `notes/jira/[PROJECT]-[KEY].md` or `projects/[PROJECT]/tickets.md`.

### 2. Epic Hierarchy Sync
- Query all Epics for the project (`issuetype = Epic AND project = [PROJECT]`).
- For each Epic, fetch child issues (`parent = [EPIC_KEY]`).
- Generate an Epic Hierarchy overview note in `notes/jira/[PROJECT]-epics.md`:

```markdown
# 🗺️ Jira Epic Hierarchy: [PROJECT]

## 🎯 Epic: [EPIC-KEY] — [Epic Summary]
- **Status:** [Status] | **Assignee:** [Name]
- **Child Tickets:**
  - 🟢 `[TICKET-KEY-1]` [Ticket Summary] ([Status])
  - 🟡 `[TICKET-KEY-2]` [Ticket Summary] ([Status])
```

### 3. My Tickets Delta Tracker
- Query tickets assigned to current user updated recently (`assignee = currentUser() AND updated >= -1d`).
- Compare status or comment changes against prior vault notes.
- Output a clean delta briefing highlighting new comments, status transitions, or blocking issues.

## Guidelines
- Always limit Jira search payloads (`limit=15-20`) to keep token consumption compact.
- Format all generated notes with clean frontmatter (`title`, `key`, `project`, `updated`).
