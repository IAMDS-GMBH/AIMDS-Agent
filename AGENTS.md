# Hermes Agent — Compact Instructions

Use this file as the repo instruction source for AI coding work. Keep it short, stable, and cache-friendly.

## Core invariants

- Prompt-cache safety is mandatory. Do not mutate past context, swap toolsets mid-conversation, or rebuild system prompt mid-session (except context compression).
- Preserve strict message-role alternation. Never inject synthetic user messages in-loop.
- Keep core tool schema narrow. New core tools are last resort because every tool is sent every call.

## Product intent

- Expand capability at edges (platforms, providers, models, UI features) while keeping core lean.
- Prefer fixing real reported bugs over speculative infra.
- Large mechanical refactors are welcome when they clearly reduce core complexity.

## Footprint ladder (choose highest viable rung)

1. Extend existing code
2. CLI command + skill
3. Service-gated tool (`check_fn`)
4. Plugin
5. MCP server in catalog
6. New core tool (only if fundamental and broadly needed)

If multiple PRs target same integration category (providers/backends/notifiers), design shared ABC + orchestrator, then plug implementations into it.

## Change quality bar

- Reproduce bug on current `main`; fix root cause and sibling paths.
- Keep behavior-safe defaults; no silent failures or broad catch-and-ignore patterns.
- Reuse existing helpers/patterns; avoid duplicate managers/hooks.
- Maintain type safety; avoid `as any` style escapes unless truly unavoidable.
- Keep edits surgical but complete; avoid unrelated drive-by changes.

## Testing and validation

- Use `scripts/run_tests.sh` (not raw `pytest`) for parity with CI.
- Python env activation (prefer `.venv`, fallback `venv`):
  - `source .venv/bin/activate`
  - `source venv/bin/activate`
- Prefer invariant tests over snapshot/change-detector tests (model names, counts, config literals).
- For integration-sensitive paths (config resolution, I/O, security boundaries, provider wiring), validate real path with real imports.

## Config policy

- Secrets only in `.env` (keys/tokens/passwords).
- Behavioral settings belong in `config.yaml`, not new user-facing `HERMES_*` env vars.
- Use profile-aware paths:
  - Code paths: `get_hermes_home()`
  - User-facing paths: `display_hermes_home()`
- Never hardcode `~/.hermes` in runtime code.

## Tooling and architecture rules

- Keep model-tool cross-references out of static schema descriptions when referenced tools may be absent; add dynamic hints in `get_tool_definitions()` logic.
- For MCP-backed memory/tools, call on demand: when users ask about projects or personal information, call the configured IAMDS server toolset first (specially the memory read/write tools); do not run memory MCP calls on every generic turn.
- For gateway running-session controls, ensure approval/control commands bypass both message guards where required.
- Avoid wiring dead/unused code into live paths without end-to-end validation.

## Plugins and memory providers

- Plugins must not patch core files with plugin-specific logic. Expand generic hooks/surface instead.
- New memory backends should be external plugin repos, not new in-tree directories under `plugins/memory/`.

## Context and instruction files

- Keep this file concise and high-signal; move long rationale and examples to docs.

## Known non-negotiables

- No destructive git operations unless explicitly requested.
- Do not break prompt caching for convenience.
- Do not replace missing real results with fabricated output.
- Do not add telemetry/attribution without explicit user-facing opt-in gate.

## Useful references (full details)

- `AGENTS.md` (full development guide)
- `toolsets.py` (toolset definitions)
- `hermes_cli/config.py` (defaults and config policy)
- `agent/coding_context.py` (coding posture behavior)
- `tests/` + `scripts/run_tests.sh` (test execution policy)
