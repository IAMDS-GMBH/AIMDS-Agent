# Automation Blueprints — AIMDS Standard

> Blueprints = fertige Automationen. Der Nutzer wählt eine, füllt 1-2 Felder,
> Hermes schedult sie als Cron-Job — **ohne Cron-Syntax** (`/blueprint <name>`).
> Ein Blueprint ist technisch ein Skill mit `metadata.hermes.blueprint`-Block.

## Standard-Blueprints
| Blueprint | Skill(s) | Default-Zeitplan | Felder | Liefert |
|---|---|---|---|---|
| `morning-brief` | digest (+ meeting-prep, email-triage) | werktags 08:00 | uhrzeit | Tagesbriefing |
| `inbox-triage` | email-triage | alle 2h (wakeAgent-Gate) | — | Cluster + Entwürfe |
| `meeting-prep` | meeting-prep | werktags 07:00 | vorlaufzeit | Briefing je Termin |
| `weekly-digest` | weekly-digest | Fr 16:00 | wochentag, uhrzeit | Wochenrückblick |

## Kosten-Disziplin
- **`wakeAgent`-Gate** für häufige Polls: ein Vorab-Skript prüft, ob sich etwas
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
