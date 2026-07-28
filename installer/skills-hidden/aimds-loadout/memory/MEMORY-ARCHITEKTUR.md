# Memory Architecture — Dual Vault System

> Dual-vault memory system for persistent cross-device memory and local project context.

## Core Vault Layers

| Vault / Layer | Types & Content | Backend / Path | Scope | Tools & Format |
|---|---|---|---|---|
| **Local Workspace Vault** | Drafts, working files, meeting notes, knowledge base (`knowledge/`) | `~/Documents/AIMDS-Suite-Vault` (Default document target) | Project / Workspace | Obsidian Markdown with `[[wikilinks]]`, `#tags`, YAML frontmatter |
| **Corporate Memory Vault** | `rule`, `profile` (address/tone), `person` (tonality), `company`, `hub` (MOCs), `project`, `task` | Central Memory MCP (`go-mcp-memory`) | Cross-device / User | `memory_save`, `memory_read`, `memory_search`, `memory_context` |
| **Knowledge Base (KB)** | Curated company knowledge, process policies, templates | Central KB MCP | Organization-wide | Read-only semantic search (`kb_search`) |

## Generic Rules & Metadata Caching

1. **Language, Address & Communication Style**: Default language is German ("Deutsch"). Preferred language (German, English, or others) and preferred address (formal "Sie" vs. informal "Du") are stored in `profile`. Person (`type: person`) and company (`type: company`) records store specific communication styles (business/formal to casual/relaxed).
2. **Knowledge Hubs (`type: hub`)**: MOCs link projects, companies, stakeholders, and tasks. Metadata serves as a lightweight cache layer to minimize context window usage.
3. **Vault-First & Clean Context**: All generated reports, drafts, and proposals are persisted to the appropriate vault before requesting user review. Only references (`[[slug]]` or file paths) and brief summaries remain in active prompt context.
4. **Prefer Local Tools**: Use local tools (`sql`, `view`, `grep`, `glob`, `edit`) for fast data processing without inflating token count.
5. **Domain Agnostic**: Works with standard suite endpoints and custom customer domains (`https://<domain>/litellm/mcp/`).
