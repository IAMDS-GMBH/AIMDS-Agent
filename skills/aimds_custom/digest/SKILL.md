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
2. **Inbox freshness check (before briefing):** Cron guarantees no ordering — if the app was closed, all overdue jobs fire together, so the inbox job may run *after* this one. Don't trust a time gap. Check `_inbox/` for items **without** a `verarbeitet:` / `processed:` frontmatter marker (i.e. unprocessed). If any exist, run the `inbox` skill first, then brief — otherwise a note captured this morning surfaces a day late.
3. **Gather data tool-driven:**
   - Use available calendar, email, task tools (`email-triage`, `PLAN.md`, `tasks/thisweek.md`, `_findings.md`, etc.).
   - **Projects:** only include projects with `projectStatus: active` or `waiting`. `waiting` is a **status reminder only**, never an action item; `dormant`/`done`/`parked` are excluded. A project's state is judged by its `projectStatus`, not by the number of open items — a project with zero open items is a good sign, not a finding.
   - **MSOffice365MCP Integration:** If MSOffice365MCP is active and connected (Outlook, Teams, OneDrive, SharePoint), query calendar events, team updates, and unread/actionable emails for the current work week.
4. **Time Horizon:** Restrict focus strictly to the current work week (Monday-Friday). Over weekends, look ahead ONLY to the next working day (Monday).
5. **Prepare Preview Notes:** Store brief notes/highlights in memory/workspace for tomorrow / the next working day so queries like "what is scheduled for tomorrow?" can be answered immediately.
6. **Prioritize:** max **3 things that matter today** (hard cap), then the rest.
7. **Deliver compactly:**
   - What matters today/this week (max 3)
   - Meetings & M365 Calendar items
   - Important emails & Teams messages (requiring action)
   - Open tasks & Next Working Day preview
8. **Weekly run requirement:** when running as weekly review, always output this concise structure — **hard cap 5 items per section**, overflow goes to `_findings.md` as a backlink:
   - **Key outcomes this week** (max 5)
   - **Carry-over items** (max 5)
   - **Insights** (0–3, optional — only if genuinely present, never forced)
   - **Next week top 3 priorities**
   - **Stale active projects (>=14 days inactivity)**
   - **Risks/open questions needing decisions** (If decision missing: `OPEN_QUESTION_NEEDED: ...`)
9. **Stay calm:** if nothing relevant exists → briefly say "nothing urgent" / "Nichts Dringendes" instead of noise.

## Output file
Write each run to `journal/YYYY-MM-DD-<kind>.md` in the active workspace, where `<kind>` is `morning-brief`, `evening-digest`, or `weekly-review`. One file per day and kind; a rerun overwrites the existing file. Frontmatter uses `type: journal` with `title`, `created`, `updated`, and `tags`.

## Escalation — counter-check before flagging "stale"
A date alone is not a finding. Before reporting an item as "open for N days", resolve the linked project and read its `projectStatus`: if `active` **and** `updated:` is younger than 14 days, do **not** escalate (the stale-looking line is likely leftover). `waiting`/`dormant`/`parked` may escalate; `done` must not. Never conclude a project is dead from a single waiting line — the project status wins.

## Writing to projects (guardrail, inline)
Before appending to a project file, read its `projectStatus`. `active`/`waiting` → append allowed. `dormant`/`done`/`parked` → **stop**: file the content as a note/knowledge entry with a `related_to: [[project]]` backlink and note it in the report. Never change `projectStatus` on your own — propose the change, the user decides.

## Self-check (before sending)
- Deadlines computed correctly against today's date?
- Every escalation counter-checked against `projectStatus` — nothing flagged on a running active project?
- Only `active`/`waiting` projects included, `waiting` as reminder only?
- "What matters today" truly ≤3 and the most important, not the first three?
- Empty sections omitted rather than filled with "none"?
- **Nothing invented** — no findings, meetings, or contacts that aren't backed by a source?

## Verification
- Top 3 are truly the highest-priority items, not just the first three.
- No completed items are reported as open.
- On an empty workspace: reports "nothing urgent" instead of inventing items.

## What NOT to do
- No wall of text. Do not auto-reply to emails — report only.
- No sales/delegation/time-tracking sections (organization-specific).
- No dashboards that can go stale.
