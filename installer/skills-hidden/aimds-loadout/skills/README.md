# Standard-Skill-Set — AIMDS Office Worker

> Skills nach offenem `agentskills.io`-Standard. Jeder Skill = Ordner + `SKILL.md`.
> Progressive Disclosure: nur Name + Beschreibung sind immer im Kontext (~80 Tokens),
> der Body wird erst geladen wenn der Skill relevant ist. "Dutzende Skills kosten
> weniger als ein aktivierter."

## Verteilung (zentral)
Single Source of Truth = ein Git-Repo `aimds-skills`. Verteilung an die Clients:
- **Pfad 1:** LiteLLM **Skills Gateway** (falls Hermes direkt daraus installieren
  kann — Gap-Test, siehe Konzept).
- **Pfad 2 (Fallback):** Git-Clone/Sync des Repos in den Hermes-Skills-Ordner.

Updates kommen so an alle Arbeitsplätze, ohne pro Gerät anzufassen.

## Die Skills
| Skill | Zweck | Tools (Toolset) |
|---|---|---|
| `deep-research` | Recherche-Synthese mit Quellenpflicht + Verifikation | web, kb |
| `meeting-prep` | Briefing aus Kalender + Doks + Web | calendar, kb, web, file |
| `email-triage` | Posteingang clustern, Aufgaben, **Entwürfe (draft-only)** | mail, file |
| `inbox` | Diktate/Nachrichten als Inbox-Workflow klassifizieren, deduplizieren, verlinken, bestätigen | file, search, mail |
| `doc-draft` | Word/PPTX/XLSX nach Firmen-Templates | file |
| `doc-review` | Dokument/Vertrag prüfen, strukturierte Findings | file, kb |
| `kb-lookup` | Firmenwissen aus AIMDS-AI-KB, mit Zitaten | kb |
| `digest` | wiederkehrende Zusammenfassung (Tag/Woche) | mail, calendar, file |

## Rollen-Add-ons (optional je Abteilung)
`sales-followup`, `crm-hygiene`, `data-cleanup` — später nachziehen.

## Konventionen
- Frontmatter: `name`, `description` (klar, triggert die Aktivierung), optional
  `metadata.hermes.blueprint` wenn der Skill als Cron-Blueprint laufen soll.
- Body: kurz, mit "Vorgehen", "Verifikation", "Was NICHT". Details in Unterdateien
  lazy nachladen.
- **Ausgabe-Format:** Skills, die ein Briefing, einen Digest, einen Report oder eine
  Triage-Zusammenfassung ausgeben, folgen `guardrails/output-format.md` (Ton,
  Status-Marker, Top-/Item-Caps, Run-Report). Kein eigenes Format erfinden.
- Self-Improving: erfolgreiche Vorgehensweisen / häufige Fehler im Skill festhalten.
