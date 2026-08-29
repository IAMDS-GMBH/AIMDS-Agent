---
name: weekly-digest
description: Weekly review digest (cron, e.g. Fri 16:00) — collects the week from tasks, findings and active projects and delivers a prepared retrospective (What went well / What stayed open / Insights / Plan for next week) saved as a journal note in the Obsidian vault. Use for "weekly review", "week in review".
metadata:
  hermes:
    blueprint:
      name: weekly-digest
      fields: [wochentag, uhrzeit]
      default_schedule: "0 16 * * 5"
---

# Weekly Digest (Weekly Review)

## Role
You prepare the weekly review: collect what happened this week and deliver a compact
retrospective that the user only needs to complement with their own reflection.
Collect data, do not embellish — be honest about open items.

## Phase 1 — Collect data
1. `tasks/thisweek.md` — what was planned, what is done, what stayed open.
2. `tasks/tasks.md` — what from the backlog became relevant this week.
3. `_findings.md` — the most important findings of the week.
4. `projects/` — **only `projectStatus: active` or `waiting`** (see
   `guardrails/project-lifecycle.md`). Per project: last change (`updated:`), open
   items, rough progress. `waiting` only as a status reminder, never as an action item.
   `dormant`/`done`/`parked` stay out. Project state is determined by `projectStatus`,
   not by the number of open items.
5. `journal/` — the most recent weekly review for context (what was last week's plan?).

## Phase 2 — Build the review
Structure and tone follow `guardrails/output-format.md`, section "Weekly review":

```
# Weekly Review YYYY-WXX

## What went well this week
- ✅ … (max 5)

## What stayed open
- 🔴 … (max 5)

## Insights (0–3, optional)
- …

## Plan for next week
1. Top priority
2. …
3. …
```

- **HARD CAP 5 items per section.** The rest goes to `_findings.md` as a backlink —
  never let a section overflow.
- **Insights only if there are real ones.** Zero insights is a valid result; do not
  force anything to fill the section.
- **Plan for next week** is a **proposal** (from open items + due project
  priorities), clearly marked as a proposal — the user decides.

## Phase 3 — File & deliver
- Write the review to `journal/YYYY-MM-DD-weekly-review.md` (`YYYY-MM-DD` = run date;
  `AGENTS.md` designates `journal/` for weekly reviews). Frontmatter per
  `_conventions.md` with `type: journal` (`title`, `created`, `updated`, `tags`).
  If the file already exists, extend it instead of creating a duplicate, and bump `updated:`.
- A compact version into the output (chat or cron response).
- Offer optionally (do not do it silently): prepare `tasks/thisweek.md` for next
  week — done items to the bottom, open ones carried over, the proposed top 3 at the top.

## Verification
- [ ] Only `active`/`waiting` projects in the review, `waiting` only as a reminder?
- [ ] No section over 5 items?
- [ ] Insights only if there really are some — otherwise section omitted?
- [ ] "Plan for next week" marked as a proposal, no unilateral status change?
- [ ] Nothing invented — no successes or findings that are not backed by evidence?
- [ ] In a quiet week honestly short instead of artificially filled?

## What NOT to do
- No time-tracking, sales or delegation section (company-specific).
- Do not change project status yourself — only propose (`guardrails/project-lifecycle.md`).
- No embellishing of open items.
