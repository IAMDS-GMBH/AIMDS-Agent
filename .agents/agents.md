# AIMDS-Agent — Instructions for AI coding work

Fork of NousResearch/hermes-agent, maintained by IAMDS. Details: `docs/REPOSITORY_STRUCTURE.md` (architecture, folders), `CONTRIBUTING.md` (adding tools/skills/plugins, cross-platform rules).

## Invariants

- Prompt-cache safety: never mutate past context, swap toolsets, or rebuild the system prompt mid-session (context compression excepted).
- Strict user/assistant role alternation; no synthetic user messages in-loop.
- Core tool schema stays narrow: every core tool is sent on every call.

## Where new capability goes

Extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP catalog entry → new core tool (last resort). Plugins never patch core files; widen generic hooks instead.

## Workflow

- Branch `feature/AIS-<nr>-<slug>` from `main`, PR into `main`, squash-merge only after review. No direct pushes.
- Conventional Commits with scope (`feat(AIS-123): …`, scopes: `desktop`, `cli`, `mcp`, `gateway`, `installer`, `skills`, `vault`). Subject lines populate the in-app update changelog, so write them for end users.

## Quality bar

- Reproduce the bug on current `main`; fix the root cause and sibling code paths.
- No silent failures or broad catch-and-ignore; behavior-safe defaults.
- Reuse existing helpers; surgical but complete edits, no drive-by changes.

## Tests

- `scripts/run_tests.sh` (per-file isolation, CI parity), never raw `pytest` for the suite.
- Invariant tests over snapshot/change-detector tests (model names, counts, config literals).
- Integration-sensitive paths (config resolution, I/O, security boundaries, provider wiring): real imports, real path.

## Config

- Secrets only in `.env`. Behavioral settings in `config.yaml`, not new user-facing `HERMES_*` env vars.
- Paths via `get_hermes_home()` (code) and `display_hermes_home()` (user-facing); never hardcode `~/.hermes`.

## Non-negotiables

- No destructive git operations unless explicitly requested.
- No fabricated results in place of missing real ones.
- No telemetry or attribution without an explicit user-facing opt-in.
- Never run `hermes update` inside this checkout: it autostashes and can `git reset --hard` on conflict (guard: `hermes_cli/config.py::is_canonical_install_location`). Use `git pull`.
