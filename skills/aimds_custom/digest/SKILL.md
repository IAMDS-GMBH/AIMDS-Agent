---
name: digest
description: Creates a recurring summary (daily or weekly digest) from calendar, inbox, open tasks, and other available project context. Usable as a cron blueprint (morning-brief / weekly-digest).
metadata:
  hermes:
    blueprint:
      name: morning-brief
      fields: [uhrzeit]
      default_schedule: "0 8 * * 1-5"
---

# Digest

## Procedure
1. **Gather data first (tool-driven):** use available calendar, email, and task
   tools/sources to collect today's/this week's meetings, important new emails
   (via `email-triage` logic), and open to-dos / `PLAN.md` status. If more
   relevant context exists (kanban, recent commits, notes), include it too.
2. **Prioritize:** max **3 things that matter today** (hard cap), then the rest.
3. **Deliver compactly:**
   - What matters today/this week (max 3)
   - Meetings
   - Important emails (only those requiring action)
   - Open tasks
4. **Weekly run requirement:** when running as weekly review, always output this concise structure:
   - **Key outcomes this week**
   - **Carry-over items**
   - **Next week top 3 priorities**
   - **Risks/open questions needing decisions**
5. **Stay calm:** if nothing relevant exists → briefly say "nothing urgent" instead of noise.

## Verification
- Top 3 are truly the highest-priority items, not just the first three.
- No completed items are reported as open.

## What NOT to do
- No wall of text. Do not auto-reply to emails — report only.
