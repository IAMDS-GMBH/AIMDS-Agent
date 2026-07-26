---
type: setup-note
audience: dev / installer
---

# Wiring the workspace template

This is the empty workspace scaffold every Hermes install starts from. It gives the
agent a **structured home** so it files things consistently from turn one — instead
of dropping files into a flat, empty `AIMDS-Suite-WorkingDirectory`.

## Why this exists

Symptom: "the agent doesn't seem to work with the vault." Cause: `terminal.cwd`
points at an empty default folder with **no structure and no AGENTS.md**. The agent
can write files but has no map of where things belong. This scaffold + AGENTS.md +
`terminal.cwd` pointing here fixes it.

## What the installer does

1. Copy this `workspace-template/` to the user's workspace location on install
   (e.g. `~/AIMDS-Workspace/` or wherever the deployment defines).
2. Set `terminal.cwd` in `config.yaml` to that path.
   ⚠ **Current config bug:** it points at `AIMDS-Suite-WorkingDirectory` — change it.
   (`seed-workspace-cwd.py` already exists for exactly this — use it.)
3. `AGENTS.md` sits in the workspace root → auto-loaded every session
   (`coding_context.py:82` reads `AGENTS.md`/`CLAUDE.md` from cwd).

## Result

- The agent reads `AGENTS.md` on every session → knows the information topology.
- The `inbox` skill files into the right folder using this structure.
- The user can open the same folder in Obsidian for graph/search/mobile — optional,
  the agent doesn't need it.

## Folders

`contacts · tasks · notes · decisions · meetings · projects` + `_inbox/_archive`
+ `_templates`. Personal work only — company knowledge lives in the KB, not here.

## For Patrick's own test now

Point a test Hermes (`HERMES_HOME=~/hermes-test`, `terminal.cwd` = a copy of this
folder) at it and dictate a few things — you'll see it file into `contacts/`,
`tasks/`, `meetings/` instead of dropping loose files.
