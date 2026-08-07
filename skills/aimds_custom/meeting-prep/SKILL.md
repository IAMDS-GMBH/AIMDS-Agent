---
name: meeting-prep
description: Builds a compact briefing for an upcoming meeting from calendar, relevant documents, and current web/company info. Use before meetings, customer appointments, calls.
metadata:
  hermes:
    blueprint:
      name: meeting-prep
      fields: [vorlaufzeit]
      default_schedule: "0 7 * * 1-5"
---

# Meeting Prep

## Procedure
1. **Get the appointment:** from the calendar (title, time, attendees, description).
2. **Gather context:** relevant emails/documents; briefly research people/companies
   (`deep-research` logic); company-internal facts via **KB** (`kb-lookup` / `kb_search`).
3. **Build the briefing** (short, scannable):
   - Who/what/when + goal of the meeting
   - 3–5 talking points
   - likely questions/objections + answers
   - open points / what the user needs to bring

## Output
Keep it scannable — one screen, no wall of text. Use status markers (🔴/🟡/⚪) only as
markers, never in prose. On a quiet calendar, say so briefly instead of padding.

## Verification
- Attendees & time match the calendar.
- No invented facts about people/companies — only what's backed by a source.

## What NOT to do
- No private/sensitive data about attendees from untrusted sources.
- Do not create/change calendar entries — brief and propose only.

## As a cron blueprint
Runs via the `blueprint` metadata in the frontmatter (default weekdays 07:00, field
`vorlaufzeit`); the user activates it with `/blueprint meeting-prep`.
