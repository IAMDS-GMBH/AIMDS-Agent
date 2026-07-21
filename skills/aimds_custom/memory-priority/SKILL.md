---
name: memory-priority
description: Establishes the MCP memory vault (memory_* / skill tools, go-mcp-memory) as the primary, cross-system store for facts, decisions, preferences, tasks, and notes. Use whenever deciding where to save or look up persistent information that should be available in future sessions or from other clients (Open WebUI, Claude Code, etc.).
---

# Memory Priority

## Why this matters
Several places could hold information: the MCP memory vault (`memory_*` / `skill` tools),
Hermes' own local memory snapshot (`memory` tool → MEMORY.md/USER.md), the local Obsidian
vault (`note-taking/obsidian` skill), or an ad-hoc file. Only the MCP vault is shared across
every client and machine — everything else lives on this one Hermes install.

## Priority order
1. **MCP memory is primary.** `memory_context`, `memory_save`, `memory_search`, `memory_read`,
   `memory_list`, `memory_backlinks`, `memory_manage`, `skill` — treat this exactly like a real
   Obsidian vault reached through MCP (the same way Claude Desktop uses `mcp-obsidian`): full
   note content, `[[wikilinks]]`, tags, backlinks, not a lightweight index. Save anything meant
   to outlive this session or be visible from other clients here first.
2. **Hermes' local memory mirror is an automatic side-effect, not something to manage.** Every
   successful `memory_save` is already mirrored into `${HERMES_HOME}/memories/` by Hermes core
   (dual-write) and folded into the next session's system-prompt snapshot. Don't also duplicate
   the save into the local `memory` tool (MEMORY.md/USER.md) unless the note is genuinely
   local-only (e.g. something about this specific machine or session).
3. **The local Obsidian vault (`note-taking/obsidian` skill, `OBSIDIAN_VAULT_PATH`) is a
   separate, filesystem-only vault — it is not synced with MCP memory.** Use it only when the
   user explicitly wants to browse/edit their own Obsidian files. If something written there
   should also persist across systems, save it via `memory_save` too — the Obsidian skill does
   not do that for you.
4. **Any other client's built-in "memory" feature (e.g. Open WebUI Personalization → Memory) is
   a fallback only.** Never treat it as the place to store something the user expects to see
   again from Hermes or another client.

## When to check memory
- Before answering a personal/project question from training data alone, check
  `memory_search` / `memory_context` first — the user may already have told a different client.
- When the user shares a fact, preference, decision, or task worth keeping, save it via
  `memory_save` rather than only the local `memory` tool — that is what makes it available in
  Hermes, Open WebUI, Claude Code, and any other MCP client.
- Call memory tools on demand (per AGENTS.md), not on every generic turn — but always before
  guessing on anything persistence-worthy.

## What NOT to do
- Don't write persistence-worthy content only to `MEMORY.md`/`USER.md` or the local Obsidian
  vault and consider it "saved" — neither syncs to other clients or machines.
- Don't build your own sync loop between the Obsidian vault and MCP memory; only mirror the
  specific note the user is working on, on request — there is no automatic bidirectional sync
  for the Obsidian vault.
- Don't edit `${HERMES_HOME}/memories/` directly — it's an automatically maintained read cache
  of MCP saves, not a place to write to by hand.
