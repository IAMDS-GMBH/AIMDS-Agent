# CLI = Co-Developer, Desktop = Co-Worker (AIS-309)

Status: shipped (2026-09). Jira: AIS-309, parent epic AIS-73.

## Why

Desktop app and terminal CLI used to share the same system prompt, `SOUL.md`,
platform config (`platform_toolsets.cli`) and skill index. Every terminal
session therefore carried the knowledge-worker loadout: ~10 KB of
Outlook/Teams/Jira/mail-safety guidance, the Chief-of-Staff identity, the 25
`aimds_custom` skills in the index, a 13 KB forced `memory_context` briefing
in the history, and 25 tool schemas including cronjob/workdays/sql/office.

Measured on a real CLI session (`20260907_161651_742786`): 12.5K tokens of
system prompt + ~12K tokens of tool schemas as the fixed per-call prefix.
Across 14 days of desktop sessions, cache **writes** (2x at 1h) cost roughly
3.5x what cache **reads** (0.1x) cost — the levers that matter are a smaller
prefix per session start, less new history per turn, and fewer re-writes of
the message tier.

## What changed

| Surface | Posture (`agent.coding_context: auto`) | Identity | Toolset | Prompt |
|---|---|---|---|---|
| Terminal CLI (`cli`) | **developer** — always | `SOUL.dev.md` (HERMES_HOME override → packaged loadout → `SOUL.md`) | `coding` + enabled MCP servers (`--toolsets` pin still wins) | no M365/Jira integration blocks; `aimds_custom` + `note-taking` skills hidden from the index; coding brief + workspace snapshot |
| Desktop / TUI (`tui`) | `coding` inside a git repo / project marker, else `general` | `SOUL.md` | platform config (`platform_toolsets.cli`) | unchanged |
| Editors (`acp`), messengers, cron | unchanged | `SOUL.md` | unchanged | unchanged |

The posture is resolved once per session (`agent.runtime_mode`,
`agent/coding_context.py`) and is immutable; the system prompt is built once
and replayed verbatim. `agent.coding_context: off` makes the CLI behave like
the desktop again; `on` forces the plain coding brief everywhere.

Session-start memory: the forced `memory_context` call in the developer
posture asks for `contexts=[coding, git, agent]` and `limit=8` — only when
the registered tool schema declares those parameters, so a foreign memory
server still gets the plain call.

Caching: `prompt_caching.message_ttl` now defaults to `1h` on interactive
surfaces (cli/tui/acp) and `5m` elsewhere; explicit config wins. `/usage`
prints the cache hit ratio, the write-vs-read cost weight and the number of
large (≥15K token) re-writes.

History budget: `*_memory_search` / `*_memory_list` results persist above
12 000 chars (preview stays inline) — the MCP result shaper deliberately
leaves memory tools alone, so a 40 KB listing used to land in the history
unshaped. `memory_read` and `memory_context` stay uncapped.

## Measured (one-shot `hermes chat -q` in this repo, copied HERMES_HOME, 2026-09-08)

| | before (session `20260907_161651`) | after (`20260908_131924`) |
|---|---|---|
| system prompt | 50 041 chars | 34 013 chars |
| tools in the schema | 25 | 22 |
| first-call cache write (prefix) | 25 008 tokens | 16 294 tokens |
| session-start `memory_context` payload | ø 13.4 KB | 18 KB — 9.3 KB of it is the 20-rule cap on the memory server |

The compact briefing arguments are sent (`contexts`, `limit`) and `limit=8`
halves the memories block, but the rules block is server-side: every rule
in the vault is tagged `coding`, so the 20-rule cap still fills. Trimming
that is vault hygiene (rule count / tags), not client work.

A second session in the same repo read 13 450 tokens from the 1h prefix
cache and wrote 853 — the developer prefix is byte-stable across sessions.

## Diagnose

```
hermes prompt-size                 # posture=developer, blocks, tool count
hermes prompt-size --platform tui  # the desktop view — must be unchanged
hermes insights --cache --days 1   # per-call cache reads/writes
/usage                             # inside a session
```

## Not in scope

A separate `desktop` platform key (desktop keeps the `cli` platform config),
compaction of the core tool schemas (they sit in the 1h prefix), trimming a
resumed history (preflight compression covers it), cron sessions.
