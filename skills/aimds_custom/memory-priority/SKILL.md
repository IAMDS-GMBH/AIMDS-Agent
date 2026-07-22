---
name: memory-priority
description: MCP memory vault (`memory_*` / `skill` tools) is the primary cross-system store. Local files (`MEMORY.md`, Obsidian) are machine-specific fallbacks.
---

# Memory Priority

- **MCP Memory (Primary):** Use `memory_save` / `memory_search` for cross-client facts, decisions, preferences, and notes.
- **Local Mirror:** `memory_save` is auto-mirrored locally by Hermes core. Do not duplicate into local `memory` tool.
- **Local Obsidian / Files:** Use only when explicitly managing local workspace files.
