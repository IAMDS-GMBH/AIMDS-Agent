---
name: obsidian-vault-manager
description: Manages the native Obsidian vault at ~/Documents/AIMDS-Suite-Vault/, auto-imports notes copied in by the user and enforces valid YAML frontmatter per _conventions.md (type, title, created, updated) plus Obsidian rendering rules for wikilinks, headings, properties, reports and journal entries.
---

# Obsidian Vault Manager

## Purpose & Approach
This skill ensures the primary workspace behaves exactly like a **native Obsidian vault** and that existing as well as newly added folders integrate seamlessly.

## Vault root & folder structure
The binding vault root is: `~/Documents/AIMDS-Suite-Vault/`

Respect and use the existing folders:
- `documents/` — analysed PDF/DOCX excerpts & reports
- `meetings/` — meeting minutes & notes
- `notes/` — short thoughts, memos & working notes
- `projects/` — project-specific subfolders & documents (canonical hubs)
- `knowledge/` — knowledge articles & references
- `decisions/` — decision records (ADRs)
- `tasks/` — task lists & to-dos
- `journal/` — daily agendas & journals
- `contacts/` — contacts & CRM excerpts
- `ideas/` — ideas & drafts
- `security/` — security reports
- `reports/` — generated data reports, one subfolder per topic (`reports/<topic>/`)
- `_inbox/` — inbox for unsorted documents
- `_templates/` — Markdown templates

## Canonical hubs & anti-duplication
1. **Deduplicate before creating:** Before creating a new note/file, ALWAYS check whether a hub or note on this topic, project or customer already exists.
2. **Single source of truth:** For every project and every focus topic there is exactly ONE canonical hub (e.g. `projects/<project-name>/<project-name>.md` or `projects/<project-name>/README.md`).
3. **Surgical updates:** New insights, worklogs or status updates are inserted into or updated in the existing sections of the existing hub. NO redundant "Copy 2" or split files are created.
4. **Hub referencing:** Detail reports link to the parent hub via wikilinks (`[[canonical-hub]]`).

## Auto-import & capture of copied-in files
When the user copies their own folders or Markdown files into the vault:
1. **Preserve folder structure:** Do not change paths; adopt the structure the user chose.
2. **Auto-indexing:** Register new Markdown files in the SQLite vector index (`VaultMetaIndex`) and create memory entries via `memory_save` where needed.
3. **Preserve wikilinks:** Keep existing Obsidian wikilinks (`[[note-name]]`).

## YAML frontmatter standard
Every file created or revised by the assistant MUST contain valid YAML frontmatter that follows `_conventions.md`:
```markdown
---
type: note
title: "Title of the note"
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: active
tags:
  - topic
related_to:
  - "[[projects/<project-name>/<project-name>]]"
---
```
- **Required keys:** `type`, `title`, `created`, `updated`. Dates as `YYYY-MM-DD` — never timestamps.
- **Optional keys:** `status` (notes: `raw` · `active` · `waiting` · `done` · `parked` · `reference`) / `projectStatus` (projects: `active` · `waiting` · `dormant` · `done` · `parked`), `tags`, `related_to`, `due`.
- **`type:` — closed vocabulary:** `project` · `knowledge` · `idea` · `decision` · `contact` · `meeting` · `document` · `security` · `hub` · `journal` · `task-list` · `note` · `automation` · `archive` · `report` · `conventions`. A value outside this list is a bug, not a variant (`task-list`, not `task`). A new value is added to `_conventions.md` first, then used.
- No `aliases` key in the standard (hubs may carry aliases — that is the template's business).

## Obsidian rendering rules
- Links carry the path: `[[projects/_hub|Projects]]`.
- No emoji in headings; status markers only (✅ 🔴 🟡 🟢 ⚠).
- Properties only from the schema above — Obsidian types a property vault-wide on first use, so a stray key or a wrong value type pollutes every note.
- Generated data reports go to `reports/<topic>/`, created from `_templates/report.md`.
- Retrospectives (morning briefs, weekly reviews) go to `journal/`.
- Never delete — archive instead (`_inbox/_archive/`, or `status: done`).

## Guardrails
- **No container paths:** NEVER write paths like `/app/data/` or Docker-internal URLs into vault files.
- **No file litter:** Never write temporary scripts (`.py`, `.sh`), JSON dumps or intermediate calculations into the vault.
- **Native:** Use plain standard Markdown with wikilinks (`[[...]]`).
