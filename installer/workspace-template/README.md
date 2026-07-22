# Workspace

This is your assistant's workspace — a folder of plain markdown files. Your Hermes
agent files everything here for you: notes, contacts, tasks, meeting recaps, decisions.
**You never have to open a file or pick a folder — just talk to the agent.**

You *can* open this folder in Obsidian, VS Code, or Finder if you want to browse it —
but you don't have to. The agent works the same either way.

## What lives where

| Folder | What goes in |
|---|---|
| `contacts/` | one file per person/company you work with |
| `tasks/` | your to-do list, with due dates |
| `notes/` | quick captures, anything that doesn't fit elsewhere |
| `ideas/` | ideas you want to keep and develop |
| `knowledge/` | **your own** reference material and learnings — "how I do X" |
| `decisions/` | decisions made, with context, dated |
| `meetings/` | meeting recaps — decisions, action items, follow-ups |
| `projects/` | ongoing work with a goal (status in frontmatter: active/waiting/dormant/done/parked) |
| `journal/` | weekly reviews + insights (the weekly-review agent files here) |
| `documents/` | generated files (Word/Excel/PDF) + attachments — kept out of the note folders so the link graph stays clean |
| `_inbox/` | staging for anything unclear (`_archive/` = processed, never deleted) |
| `_templates/` | the shapes the agent uses when creating new files |
| `_conventions.md` | frontmatter schema, naming, tag vocabulary — keeps everything consistent |
| `tasks/thisweek.md` | your current focus — the agent loads this every session so it knows what matters now |
| `_findings.md` | what the agent noticed in the background — you can open it any time |
| `_open-questions.md` | things the agent needs clarified — captured here, not lost in chat |

**`knowledge/` is *your* knowledge.** Shared **company** knowledge (offer standards,
processes, how the firm works) does **not** live here — that's the company knowledge
base, changed only through the curator agent. This workspace is yours.

## How the agent works with it

Defined in `AGENTS.md` — the agent's operating manual. It's always loaded and tells
the agent where each kind of information belongs.
