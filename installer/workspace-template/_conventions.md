---
type: conventions
---

# Conventions

> How the agent keeps the workspace clean and consistent. Prevents the exact rot that
> hurts an ungoverned knowledge base (missing frontmatter, tag sprawl, duplicates).

## Frontmatter (every file)

Required: `type`, `title`, `created`, `updated`.
As applicable: `status` / `projectStatus`, `tags`, `related_to`, `due`.
Format: YAML between `---` markers. Dates as `YYYY-MM-DD`.

## File names

kebab-case, no umlauts, descriptive. One idea/entity = one file. Never a catch-all.

## Links

Relate every new file to ≥1 existing one with `[[wikilinks]]`. This is the thinking
substrate — links are how the agent (and the user) find related things later.

## Status vocabulary — closed vocabulary, no exceptions

- Notes: `raw` · `active` · `waiting` · `done` · `parked` · `reference`
- Projects (`projectStatus`): `active` · `waiting` · `dormant` · `done` · `parked`
- `type`: `project` · `knowledge` · `idea` · `decision` · `contact` · `meeting` · `hub` ·
  `journal` · `task-list` · `automation` · `archive`

A value not in this list is a bug, not a variant. New value needed? Add it here
first, then use it. (Reference vault had 51 type variants before enforcement —
no agent could filter reliably.) Full schema + decay rules: `HARNESS.md` §1+§3.

Projects: `due:` may be empty but never omitted; empty `due:` requires `due-reason:`
so a missing date is distinguishable from a forgotten one.

## Tags — keep the vocabulary small

Prefer an existing tag over inventing a new one. Suggested starter set (extend
deliberately, not per-note):

`customer` · `internal` · `offer` · `meeting` · `decision` · `idea` · `followup` ·
`finance` · `hr` · `it` · `personal`

A tag that's used once is noise. When unsure, don't tag — the folder + links already
carry meaning.

## Filing map

This folder structure + `AGENTS.md` is the one authoritative "where does what go"
map. Before creating a new folder, check whether one already exists — never make a
second `invoices/` next to `Rechnungen/`. New top-level folders only when the map
genuinely lacks a home.

## New connectors / tools — security gate

Before connecting a new tool, connector, or installing an add-in: check the source
and what permissions it asks for. When in doubt, ask the user rather than connecting
blindly. (~26% of community skills carry vulnerabilities — the agent is the user's
supply chain.)

## Never

- Delete — archive instead (`_inbox/_archive/`, or `done` status).
- Create a near-duplicate — extend the existing file and bump `updated:`.
- Leave a file without frontmatter.
- Change more than what was asked (no silent reformatting/renaming on the side).
