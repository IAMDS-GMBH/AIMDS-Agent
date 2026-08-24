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
- `projects/` — Projektspezifische Unterordner & Dokumente (Canonical Hubs)
- `knowledge/` — Wissensartikel & Referenzen
- `decisions/` — Entscheidungsdokumentation (ADRs)
- `tasks/` — Aufgabenlisten & To-Dos
- `journal/` — Tagesagenden & Journale
- `contacts/` — Ansprechpartner & CRM-Exzerpte
- `ideas/` — Ideen & Entwürfe
- `security/` — Sicherheitsberichte
- `_inbox/` — Eingangskorb für unsortierte Dokumente
- `_templates/` — Markdown-Schilder & Vorlagen

## Canonical Hubs & Anti-Duplikation
1. **Deduplizierung vor Neuanlage:** Vor dem Erstellen einer neuen Notiz/Datei MUSS immer geprüft werden, ob bereits ein Hub oder eine Notiz zu diesem Thema, Projekt oder Kunden existiert.
2. **Single Source of Truth:** Für jedes Projekt und jeden Themenschwerpunkt existiert genau EIN kanonischer Hub (z. B. `projects/<Projektname>/<Projektname>.md` oder `projects/<Projektname>/README.md`).
3. **Chirurgische Updates:** Neue Erkenntnisse, Worklogs oder Status-Updates werden gezielt in bestehende Abschnitte des existierenden Hubs eingefügt oder aktualisiert. Es werden KEINE redundanten "Copy 2" oder Split-Dateien angelegt.
4. **Hub-Referenzierung:** Detailberichte verlinken mit Wikilinks (`[[Kanonischer-Hub]]`) auf den übergeordneten Hub.

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
type: note | document | meeting | project | decision | task | contact | idea | knowledge | journal | security
tags:
  - thema
  - kategorie
aliases:
  - "Alternativer Titel"
---
```

## Guardrails
- **Keine Container-Pfade:** Schreibe NIE Pfade wie `/app/data/` oder Docker-Interne URLs in Vault-Dateien.
- **Kein Dateimüll:** Schreibe niemals temporäre Skripte (`.py`, `.sh`), JSON-Dumps oder Zwischenberechnungen in den Vault.
- **Nativität:** Nutze reines Standard-Markdown mit Wikilinks (`[[...]]`).
