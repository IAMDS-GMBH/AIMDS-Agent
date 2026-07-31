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

## Commit and changelog guidelines

- Follow Conventional Commits format: `<type>(<scope>): <short imperative summary>`.
  - Common types: `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `chore`, `build`, `ci`.
  - Common scopes: `desktop`, `cli`, `mcp`, `gateway`, `installer`, `skills`, `vault`.
- Write subject lines in the imperative mood ("add", "fix", "remove" — not "added" or "fixing").
- Keep subject lines under 50–72 characters without trailing period.
- User-facing impact: commit messages directly populate the in-app update changelog (`readCommitLog`); make subject lines human-readable, describing *what* changed and *why* for end users.

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
- Canonical AIMDS Suite provider instance keys are `aimds-suite-prod` (`suite.iamds.com`), `aimds-suite-staging` (`staging.suite.iamds.com`), and `aimds-suite-dev` (`dev.suite.iamds.com`). Custom domains (e.g. `https://<custom-domain>/litellm/mcp/`) are dynamically resolved from the configured provider `base_url`. Legacy `iamds-litellm*` aliases are preserved for backward compatibility.
- AIMDS Suite endpoints handle prompt caching server-side (via `prompt_cache_hook.py`); Hermes client-side `cache_control` injection is omitted (`anthropic_prompt_cache_policy` returns `False`) to prevent breakpoint conflicts.
- Request timeouts default to 180s in `config.yaml` for AIMDS Suite endpoints.
- API calls scale timeouts exponentially on retries (`2 ** retry_count` multiplier). Live progress status notifications must inform the user during retry attempts, and terminal API errors must be formatted as friendly, actionable messages rather than raw technical stack/string dumps.
- Use profile-aware paths:
  - Code paths: `get_hermes_home()`
  - User-facing paths: `display_hermes_home()`
- Never hardcode `~/.hermes` in runtime code.

## Tooling and architecture rules

- Keep model-tool cross-references out of static schema descriptions when referenced tools may be absent; add dynamic hints in `get_tool_definitions()` logic.
- For MCP-backed memory/tools, call on demand: when users ask about projects or personal information, call the configured AIMDS server toolset first (specially the memory read/write tools); do not run memory MCP calls on every generic turn.
- **Dual-Memory Partitioning**:
  - **Memory MCP**: Stores concise global rules, directives, preferences, and high-level architectural invariants (< 1-2 KB per entry).
  - **Local Vault Index / Brain / Obsidian (`.md` files)**: Stores detailed documentation, specifications, logs, and changelogs. Never store large documents in Memory MCP; write them to `.md` files indexed via local vector search.
- **Chat Response & Preview Efficiency**:
  - Keep chat responses concise.
  - When generating or updating large files/documents (e.g. changelogs, reports), do NOT dump full file content into chat output. Provide a short 2-3 line summary and path reference so the desktop preview UI renders the file.
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

## Repository map

- `agent/` — core agent loop, coding posture, context/session management.
- `tools/` — built-in tool implementations + tool discovery/search (`tool_search.py`, `mcp_tool.py`).
- `toolsets.py` — toolset definitions; wires tools into platform presets.
- `providers/` — model-provider integrations and auth flows.
- `plugins/` — optional plugin surface (e.g. memory backends); must not patch core.
- `optional-mcps/`, `optional-skills/` — bundled but opt-in MCP servers / skills.
- `skills/` — bundled skills, organized by category.
- `hermes_cli/` — CLI entrypoints, config resolution, defaults.
- `apps/desktop/` — Electron/Next.js desktop client.
- `gateway/`, `tui_gateway/`, `ui-tui/` — gateway and terminal UI surfaces.
- `cron/`, `acp_adapter/`, `acp_registry/` — scheduling and agent-communication-protocol support.
- `docker/`, `packaging/`, `installer/`, `scripts/` — build, packaging, and installer tooling.
- `tests/` + `scripts/run_tests.sh` — test suite and CI-parity test runner.
- `docs/` — longer-form documentation and rationale.

## Useful references (full details)

- `.agents/agents.md` (this file — full development guide)
- `CONTRIBUTING.md` (contribution workflow, adding tools/skills/plugins)
- `toolsets.py` (toolset definitions)
- `hermes_cli/config.py` (defaults and config policy)
- `agent/coding_context.py` (coding posture behavior)
- `tests/` + `scripts/run_tests.sh` (test execution policy)
