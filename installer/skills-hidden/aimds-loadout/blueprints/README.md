# Automation Blueprints — AIMDS Standard

> Blueprints = fertige Automationen. Der Nutzer wählt eine, füllt 1-2 Felder,
> Hermes schedult sie als Cron-Job — **ohne Cron-Syntax** (`/blueprint <name>`).
> Ein Blueprint ist technisch ein Skill mit `metadata.hermes.blueprint`-Block.

## Standard-Blueprints
| Blueprint | Skill(s) | Default-Zeitplan | Felder | Liefert |
|---|---|---|---|---|
| `morning-brief` | Collector + digest (Compose-Contract) | werktags 08:00 | uhrzeit | Tagesbriefing |
| `inbox-triage` | email-triage | alle 2h (wakeAgent-Gate) | — | Cluster + Entwürfe |
| `meeting-prep` | meeting-prep | werktags 07:00 | vorlaufzeit | Briefing je Termin |
| `weekly-digest` | weekly-digest | Fr 16:00 | wochentag, uhrzeit | Wochenrückblick |

## Kosten-Disziplin
- **Collector statt Agent-Recherche (AIS-305):** Die mitgelieferten Jobs
  `morning-brief`, `weekly-review`, `m365-mail-check`, `m365-teams-check` tragen das
  Feld `collector`. Der Scheduler sammelt vor dem LLM-Lauf deterministisch
  (`cron/brief_collector.py`, Adapter in `cron/brief_sources/`): prüft per
  `get_mcp_status`, welche MCP-Server verbunden sind, ruft **nur user-bezogene,
  gebündelte** Abfragen ab (M365 `m365_brief_snapshot`, Jira `assignee = currentUser()`
  mit `fields`/`limit`, Tempo `retrieveWorklogs`), schreibt die Items nach
  `state.db` (`brief_items`, `brief_runs`) und injiziert einen `## Collected Data`-Block.
  Der Agent läuft ohne Tools (`compose_max_iterations`, Default 3) und der Scheduler
  schreibt das Journal (`journal/YYYY-MM-DD-<kind>.md`, Obsidian-Frontmatter, Hub-Link).
  Sprache: `cron.brief_collector.language` → `display.language` → `en`.
  Mail-/Teams-Check: nichts Neues → `[SILENT]` ohne LLM-Lauf (0 Token).
  Kostenzeile je Lauf im Log: `[AIS-161] cron cost: job=… api_calls=… in=… out=…`.
- **`cron.background_review: false`** (Default): kein Memory-/Skill-/Tool-Findings-Review
  nach Cron-Läufen — der Review-Fork kostete pro Morning Brief ~400k Tokens.
- **`wakeAgent`-Gate** für eigene Polls: ein Vorab-Skript prüft, ob sich etwas
  geändert hat (neue Mail) — nur dann wacht der Agent auf (sonst 0 Token).
- **`enabled_toolsets` pro Job** einschränken (nur die nötigen Toolsets).
- **`context_from`** für Ketten (collect → rank → deliver), wenn nötig.
- **`[SILENT]`** für Monitoring-Jobs, die nur bei Auffälligkeit melden sollen.

## Voraussetzung
Der **Gateway-Daemon** muss laufen (`hermes gateway install`), sonst feuern keine
Cron-Jobs. Bei Desktop-Usern: als User-Service einrichten oder serverseitig hosten.

## Lieferweg
Blueprints sind Skills → leben im zentralen `aimds-skills`-Repo und werden mit dem
Skill-Set ausgerollt. Der Nutzer aktiviert sie selbst über `/blueprint` oder die
Dashboard-Blueprints-Tab.
