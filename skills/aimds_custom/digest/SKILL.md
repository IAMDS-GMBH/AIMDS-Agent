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
1. **Language Awareness:** Respond in the user's preferred language (German if the system/context is in German, English otherwise).
2. **Gather data tool-driven:**
   - Use available calendar, email, task tools (`email-triage`, `PLAN.md`, etc.).
   - **MSOffice365MCP Integration:** If MSOffice365MCP is active and connected (Outlook, Teams, OneDrive, SharePoint), query calendar events, team updates, and unread/actionable emails for the current work week.
3. **Time Horizon:** Restrict focus strictly to the current work week (Monday-Friday). Over weekends, look ahead ONLY to the next working day (Monday).
4. **Prepare Preview Notes:** Store brief notes/highlights in memory/workspace for tomorrow / the next working day so queries like "what is scheduled for tomorrow?" can be answered immediately.
5. **Prioritize:** max **3 things that matter today** (hard cap), then the rest.
6. **Deliver compactly:**
   - What matters today/this week (max 3)
   - Meetings & M365 Calendar items
   - Important emails & Teams messages (requiring action)
   - Open tasks & Next Working Day preview
7. **Weekly run requirement:** when running as weekly review, always output this concise structure:
   - **Key outcomes this week**
   - **Carry-over items**
   - **Next week top 3 priorities**
   - **Stale active projects (>=14 days inactivity)**
   - **Risks/open questions needing decisions** (If decision missing: `OPEN_QUESTION_NEEDED: ...`)
8. **Stay calm:** if nothing relevant exists → briefly say "nothing urgent" / "Nichts Dringendes" instead of noise.

## Verification
- Top 3 are truly the highest-priority items, not just the first three.
- No completed items are reported as open.

## What NOT to do
- No wall of text. Do not auto-reply to emails — report only.
