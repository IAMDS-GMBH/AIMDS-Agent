---
name: feierabend-digest-agent
description: Automated end-of-day digest (cron at 17:00) that summarizes the day, completed and open tasks, and pending Outlook email drafts into a journal file.
---

# Feierabend Digest Agent (Cron)

## Operation & cron flow
This agent runs in the background at the end of the working day (e.g. 17:00 via Hermes Cron Execution). The user may also trigger it manually by saying "Feierabend".

1. **Task review:** Check completed and open to-dos in `~/Documents/AIMDS-Suite-Vault/tasks/`.
2. **Mail & draft status:** Record the email drafts created that are awaiting release.
3. **Daily summary:** Write the evening digest to `journal/YYYY-MM-DD-evening-digest.md` in the active workspace.

## Output file
- Path: `journal/YYYY-MM-DD-evening-digest.md`, one file per day; a rerun on the same day overwrites the file.
- Frontmatter (`type: journal`):
```markdown
---
title: "Evening digest YYYY-MM-DD"
type: journal
created: YYYY-MM-DDTHH:MM:SS
updated: YYYY-MM-DDTHH:MM:SS
tags:
  - journal
  - evening-digest
---
```

## Output format
Tone, status markers, and structure follow `guardrails/output-format.md`. On a quiet
day, briefly state "nothing open" instead of inventing a list.
