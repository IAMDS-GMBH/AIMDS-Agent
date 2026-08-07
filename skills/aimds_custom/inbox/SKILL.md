---
name: inbox
description: Processes incoming dictations/messages reliably as an inbox workflow — classify, deduplicate against the source URL, extend an existing entry or create a new one, link, and confirm. Hardened against parallel runs, duplicate creation, and silent failures.
---

# Inbox Workflow (dictations & messages)

## Goal
Move incoming dictations/messages into the workspace **reproducibly** — no silent
failures, no duplicate entries, and no second run that files the same input twice.

## Phase 0 — run lock (very first action)
Two parallel runs can process the same inbox file and create two entries for the same
content. So take a lock before anything else.

Lock file: `_inbox/.inbox-lock` (transient run coordination, not a run log).

1. Does the lock file exist?
   - **No** → write the lock (one line: `<ISO timestamp> · <trigger>`, trigger =
     `scheduled` | `briefing` | `interactive`), continue.
   - **Yes, timestamp younger than 30 min** → **abort the run.** Touch nothing, no
     archiving, output only one line "skipped — another run active".
   - **Yes, timestamp older than 30 min** → treat as orphaned (crashed prior run),
     overwrite it, note `⚠ overwrote orphaned lock` in the run report.
2. **At the end of the run (mandatory, even on error):** delete the lock file. `rm` of
   your own lock file is explicitly allowed here.

> 30 min is longer than the longest realistic run but short enough that a crash doesn't
> block the next run until the next day. The lock prevents **concurrency** — against
> duplicate creation after a partial abort, the source-URL check below is what protects.

## Phase 1 — process (mandatory order per item)

### 1. Set idempotency marker (before processing)
- If `processing_started:` is already in the frontmatter but the file is **not** archived,
  a prior run aborted — check carefully what was already created instead of blindly
  re-creating.
- Otherwise write `processing_started: YYYY-MM-DDTHH:mm` to the frontmatter **immediately**,
  before processing anything.

### 2. Classify
Determine type, topic, priority, and intended action. Always read the routing target from
the routing table in the active workspace's `AGENTS.md` — no hard-coded route mappings in
the skill text or tool arguments.

### 3. Check existing — duplicate check against the source URL (not the filename)
A filename comparison is not enough: the same content gets two different but plausible
slugs from two runs and lands twice. If the item carries a `url:` in frontmatter:
1. Normalize the URL to its stable identifier (ignore query params, trailing slash, and
   `www.`; for platform links take the shortcode/post identifier).
2. Grep across `knowledge/`, `ideas/`, `projects/`, `contacts/`, `notes/`, `decisions/`
   for that identifier.
3. **Match → do not create anew**; extend the existing file and set `updated:`.
4. No match → create. The source URL **must** go into the frontmatter, or the check won't
   fire on the next run.

### 4. Extend or create
Extend a duplicate/continuation; otherwise create. For project notes, read the target
project's `projectStatus` **before** writing: `active`/`waiting` → append allowed;
`dormant`/`done`/`parked` → **stop**, file it as a note/knowledge entry with a
`related_to: [[project]]` backlink and note it in the report. Never change `projectStatus`
yourself — propose it, the user decides.

### 5. Ensure a core insight (gate before archiving)
An item may only be archived **once its topic is extracted and filed.** Before archiving:
1. Was a target file created **or** an existing one meaningfully extended?
2. Does it contain a clearly stated core insight (1–3 sentences: what is the topic, why
   relevant)?

No to 1 or 2 → **do not archive.** For CTA/funnel content especially, the visible message
("DM me X", "link in bio") is not the topic — the topic is what the content actually talks
about. Extract first, then archive.

### 6. Auto-linking
Add at least one relevant existing `[[wikilink]]` if candidates exist. If none exist, state
that explicitly as the result.

### 7. Archive (never delete)
After **successful** processing: write `verarbeitet: YYYY-MM-DD` / `processed: YYYY-MM-DD`
plus the target path to the frontmatter, then move to `_inbox/_archive/`. Never delete. The
`verarbeitet:` marker is also the signal the morning digest uses to detect inbox freshness.

### 8. Confirm
Report to the user briefly and unambiguously **what** was filed/extended **where**, and
which links were set. No silent completion.

## Error paths — handle explicitly
- **Classification unclear** → targeted question instead of guessing; or park with
  `needs-triage` in `_inbox/`.
- **Processing fails** → write `error: <description>` + `error_date: YYYY-MM-DD` to the
  frontmatter, increment `attempts:`, leave the file in `_inbox/`, move on. **No silent
  abort**, no success message.
- **Attempt tracking:** at `attempts: 3` stop retrying endlessly — file the raw content as
  a note with `status: raw` (target per routing table), archive the item, and note in the
  report "filed as raw note after 3 attempts".
- **No link candidate** → state it explicitly as the result.

## Verification (self-check before finishing)
- Run lock set and deleted again at the end?
- Duplicate check ran against `url:` (not just the filename)?
- On a duplicate, the existing file was extended (no second new entry)?
- Every archived item has a core insight in its target file?
- At least one relevant link set where candidates existed?
- Failed items marked with `error:`/`attempts:`, not silently swallowed?
- Short, unambiguous completion confirmation to the user?

## What NOT to do
- No silent fallbacks without feedback.
- No invented sources/links.
- No deleting — always archive.
- No sending external messages without explicit approval.
