---
name: obsidian-vault-manager
description: Verwaltet den nativen Obsidian Vault unter ~/Documents/AIMDS-Suite-Vault/, führt Auto-Imports reinkopierter Notizen durch und erzwingt valides YAML Frontmatter.
---

# Obsidian Vault Manager

## Zweck & Arbeitsweise
Dieser Skill stellt sicher, dass sich der primäre Arbeitsbereich exakt wie ein **nativer Obsidian Vault** verhält und bestehende sowie neu hinzugefügte Ordner nahtlos integriert werden.

## Vault-Root & Ordnerstruktur
Der verbindliche Vault-Root ist: `~/Documents/AIMDS-Suite-Vault/`

Respektiere und nutze die vorhandenen Ordner:
- `documents/` — Analysierte PDF/DOCX-Exzerpte & Berichte
- `meetings/` — Meeting-Protokolle & Notizen
- `notes/` — Kurze Gedanken, Memos & Arbeitsnotizen
- `projects/` — Projektspezifische Unterordner & Dokumente
- `knowledge/` — Wissensartikel & Referenzen
- `decisions/` — Entscheidungsdokumentation (ADRs)
- `tasks/` — Aufgabenlisten & To-Dos
- `journal/` — Tagesagenden & Journale
- `contacts/` — Ansprechpartner & CRM-Exzerpte
- `ideas/` — Ideen & Entwürfe
- `security/` — Sicherheitsberichte
- `_inbox/` — Eingangskorb für unsortierte Dokumente
- `_templates/` — Markdown-Schilder & Vorlagen

## Auto-Import & Erfassung reinkopierter Dateien
Wenn der Nutzer eigene Ordner oder Markdown-Dateien in den Vault kopiert:
1. **Ordnerstruktur erhalten:** Ändere keine Pfade, sondern übernehme die vom Nutzer gewählte Struktur.
2. **Auto-Indexierung:** Registriere neue Markdown-Dateien im SQLite Vector Index (`VaultMetaIndex`) und erstelle bei Bedarf Memory-Einträge via `memory_save`.
3. **Wikilinks erhalten:** Erhalte bestehende Obsidian-Wikilinks (`[[Notizname]]`).

## YAML Frontmatter Standard
Jede vom Assistenten erstelle oder überarbeitete Datei MUSS valides YAML Frontmatter enthalten:
```markdown
---
title: "Titel der Notiz"
created: YYYY-MM-DDTHH:MM:SS
updated: YYYY-MM-DDTHH:MM:SS
type: note | document | meeting | project | decision | task
tags:
  - thema
  - kategorie
aliases:
  - "Alternativer Titel"
---
```

## Guardrails
- **Keine Container-Pfade:** Schreibe NIE Pfade wie `/app/data/` oder Docker-Interne URLs in Vault-Dateien.
- **Nativität:** Nutze reines Standard-Markdown mit Wikilinks (`[[...]]`).
