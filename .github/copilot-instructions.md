# Copilot instructions

Full development guide: [`.agents/agents.md`](../.agents/agents.md). Read it before
making changes in this repository — it covers prompt-cache invariants, the
footprint ladder for where new capabilities belong, change-quality bar, test
commands, and config policy.

Quick pointers:

- Run tests with `scripts/run_tests.sh` (not raw `pytest`) for CI parity.
- Prefer extending existing code, adding a CLI command/skill, or a plugin over
  adding new core tools — see the footprint ladder in `.agents/agents.md`.
- Reproduce bugs on `main` first; fix root cause and sibling paths, not just
  the reported symptom.
