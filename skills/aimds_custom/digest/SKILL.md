---
name: digest
description: Compose contract for the daily morning brief and the weekly review (interactive use; the scheduled runs get their data from the LLM-free cron collector). Usable as a cron blueprint (morning-brief / weekly-digest).
metadata:
  hermes:
    blueprint:
      name: morning-brief
      fields: [uhrzeit]
      default_schedule: "0 8 * * 1-5"
---

# Digest

Scheduled runs (`aimds-morning-brief`, `aimds-weekly-review`) do **not** gather data
themselves: the scheduler's collector (`cron/brief_collector.py`) fetches calendar,
unread mail, tickets, worklogs and workspace signals deterministically, injects a
`## Collected Data` block and writes the journal file. This skill is the compose
contract for that block and for an interactive "give me my brief" request.

## Language
Exactly one language per brief — the `LANGUAGE:` line of the run (config
`display.language`, default English). Never mix.

## Morning brief — structure
1. **What matters today** — hard cap 3, the most important items, not the first three.
2. **Meetings** — today and the next working day (time, subject, location).
3. **My tickets** — due or updated (max 5) and "changed since the last brief".
4. **Mail & Teams needing action** — max 5, one line each.
5. **Open tasks & time tracking** — open tasks, logged vs. target hours.
6. **Preview: next working day**.

Omit empty sections. Nothing relevant → "nothing urgent" / "Nichts Dringendes".

## Weekly review — structure (hard cap 5 per section)
Key outcomes · Carry-over items · Next week top 3 priorities · Stale active projects
(≥14 days, from `## Stale Project Check`) · Risks/open questions needing decisions.

## Rules
- Compose only from the collected data; never invent meetings, mails, tickets or contacts.
- Name skipped or failed sources in one line ("Outlook not connected").
- Plain headings without emoji; status markers (✅ 🔴 🟡 🟢 ⚠) only in lists/tables.
- Do not write files and do not output frontmatter in cron runs — the scheduler does.
- Interactive runs: write to `journal/YYYY-MM-DD-<kind>.md` with the vault frontmatter
  schema (`type: journal`, `title`, `created`, `updated`, `tags`), overwrite on rerun.
- End with the marker block: `FINDING: …`, `NEXT: …`, and only if a user decision is
  missing `OPEN_QUESTION: …`. Never omit FINDING.

## Self-check
- Top 3 truly the highest priority? Deadlines computed against today's date?
- Nothing reported as open that the data shows as done?
- Tone and format per `guardrails/output-format.md`.
