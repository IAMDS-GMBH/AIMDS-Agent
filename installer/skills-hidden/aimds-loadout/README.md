# Hermes Office-Worker Loadout — AIMDS-Suite

> Standard-Auslieferung für jede Hermes-Agent-Installation beim Kunden.
> Ziel: ein nicht-technischer Office-Worker installiert Hermes, bekommt dieses
> Loadout, und ist **out of the box produktiv** mit der AIMDS-Suite.
> Ansatz: goal-getriebener, selbst-verifizierender Harness nach 2026-Best-Practice
> (Anthropic Context Engineering, 12-Factor Agents, ReAct/Reflexion) — kein
> Datei-Routing-Klon.

## Was hier drin liegt

```
Hermes-AIMDS-Loadout/
├── README.md                     ← dieses Dokument (Deploy-Anleitung)
├── identity/
│   └── SOUL.md                   ← globale Identität/Persona  → ~/.hermes/SOUL.md
├── workspace/                    ← Default-Arbeitsordner ("Company-Workspace")
│   ├── AGENTS.md                 ← Herzstück: Routing, Goal/PLAN-Regel, Disziplin
│   └── PLAN.template.md          ← Vorlage für die Recitation-/Plan-Datei
├── memory/
│   ├── MEMORY-ARCHITEKTUR.md     ← WIE Memory funktioniert (3 Schichten)
│   ├── USER.seed.md              ← Seed für das User-Profil
│   └── MEMORY.seed.md            ← Seed für die Agent-Notizen
├── skills/                       ← Standard-Skill-Set (agentskills.io)
│   ├── README.md
│   ├── deep-research/SKILL.md
│   ├── meeting-prep/SKILL.md
│   ├── email-triage/SKILL.md
│   ├── doc-draft/SKILL.md
│   ├── doc-review/SKILL.md
│   ├── kb-lookup/SKILL.md
│   └── digest/SKILL.md
├── blueprints/
│   └── README.md                 ← Cron-Blueprints (morning-brief etc.)
├── guardrails/
│   └── tool-risk-registry.md     ← Risk-Rating + Freigabe-Regeln pro Tool
└── config/
    └── config.hermes.example.yaml← Beispiel ~/.hermes/config.yaml
```

## Die 8 Schichten (Kurzüberblick)

| # | Schicht | Datei | Zentral via LiteLLM? |
|---|---|---|---|
| 1 | Identität | `identity/SOUL.md` | nein (Installer) |
| 2 | Ziel-Scaffold | `workspace/AGENTS.md` (+ `/goal`) | nein |
| 3 | Verifikations-Loop | Goal-Judge (built-in) | Judge-Modell via LiteLLM |
| 4 | Context/Routing | `workspace/AGENTS.md` | nein |
| 5 | Skills | `skills/` | **ja — Git-Sync (repo-basiert)** |
| 6 | Memory + Self-Improve | `memory/` | **ja — User-Memory-MCP** |
| 7 | Guardrails | `guardrails/` | teils (Policies) |
| 8 | Tools + Wissen | `config/` (MCP) | **ja — MCP Gateway + AIMDS-AI-KB** |

## Was schon steht (Stand 2026-06)
- ✅ **Inference** über LiteLLM Model-Endpoint
- ✅ **MCP Gateway** über LiteLLM — **AIMDS-AI-KB dahinter geklemmt** (Firmenwissen)
- ✅ **User-basierter Memory-MCP** über LiteLLM — pro User in DB, cross-device abrufbar
- ✅ **Memory Gateway** der LiteLLM-Schnittstelle angebunden

## Deploy pro Arbeitsplatz (Soll-Ablauf)

1. **Hermes installieren** (Desktop, Mac/Windows).
2. **`identity/SOUL.md`** → nach `~/.hermes/SOUL.md` kopieren.
3. **`config/config.hermes.example.yaml`** → angepasst nach `~/.hermes/config.yaml`
   (LiteLLM-Endpoint, Goal-Judge-Modell, `write_approval`, MCP-Server).
4. **Company-Workspace** anlegen: `workspace/` als Default-Arbeitsordner des Users
   ablegen (enthält `AGENTS.md`). Hermes lädt `AGENTS.md` automatisch beim Start.
5. **Skills** installieren — **repo-synced** über das zentrale `aimds-skills`-Git-Repo
   als einzige aktive Quelle; nicht via lokale Installer-Kopie und nicht via
   ad-hoc Skill-Hub/Marketplace-Downloads (siehe `skills/README.md`).
6. **Memory** seeden — `USER.seed.md` + `MEMORY.seed.md` als Startpunkte für lokale
   Defaults; das durable User-Profil lebt zentral im User-Memory-MCP
   (siehe `memory/MEMORY-ARCHITEKTUR.md`).
7. **Gateway-Daemon** sicherstellen (`hermes gateway install`) — sonst keine
   Cron-Jobs / Goals im Hintergrund.
8. **Blueprints** anbieten — `/blueprint morning-brief` etc. (siehe `blueprints/`).

## Offene Punkte (vor Pilot klären)
- ✅ **Skills-Distribution festgelegt:** Git-Sync aus `aimds-skills` ist der
  verbindliche Pfad für aktive Skills.
- ⚠ **Daemon**: läuft der Gateway-Daemon dauerhaft am Desktop, oder serverseitig?
- ⚠ **Datenresidenz**: Nous-eigene Tools (Web/Image/Browser) meiden, alles über
  eigene MCP/LiteLLM-Backends.

Konzept-Hintergrund: `patrick-brain/knowledge/ai-tech/hermes-aimds-customer-loadout.md`
