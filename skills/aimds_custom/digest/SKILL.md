---
name: digest
description: Creates a recurring summary (daily or weekly digest) from calendar, inbox, and open tasks. Usable as a cron blueprint (morning-brief / weekly-digest).
metadata:
  hermes:
    blueprint:
      name: morning-brief
      fields: [uhrzeit]
      default_schedule: "0 8 * * 1-5"
---

# Digest

## Procedure
1. **Gather data:** today's/this week's meetings, important new emails (via
   `email-triage` logic), open to-dos / `PLAN.md` status.
2. **Prioritize:** max **3 things that matter today** (hard cap), then the rest.
3. **Deliver compactly:**
   - What matters today/this week (max 3)
   - Meetings
   - Important emails (only those requiring action)
   - Open tasks
4. **Stay calm:** if nothing relevant exists → briefly say "nothing urgent" instead of noise.

## Verification
- Top 3 are truly the highest-priority items, not just the first three.
- No completed items are reported as open.

## What NOT to do
- No wall of text. Do not auto-reply to emails — report only.
