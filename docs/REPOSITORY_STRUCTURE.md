# Repository-Struktur & Architektur – AIMDS-Suite Agent

Dieses Dokument bietet einen Überblick über die Ordnerstruktur, Komponenten und deren Zusammenspiel im Repository **AIMDS-Agent**.

---

## 1. Übersicht & Architektur-Zusammenspiel

Das System besteht im Wesentlichen aus 4 Hauptschichten:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Client-Schicht                                  │
│  ┌───────────────────────────┐         ┌─────────────────────────────────┐  │
│  │   apps/desktop (Electron) │         │ apps/bootstrap-installer (Tauri)│  │
│  │   • React + Tailwind GUI  │         │ • Rust Native Installer/Updater │  │
│  │   • Multi-Window / Chat   │         │ • Keycloak SSO / Config Setup   │  │
│  └─────────────┬─────────────┘         └────────────────┬────────────────┘  │
│                │ JSON-RPC / WebSocket                   │                   │
└────────────────┼────────────────────────────────────────┼───────────────────┘
                 ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Backend & Runtime Gateway                          │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  tui_gateway/ & gateway/ & hermes_cli/                                │  │
│  │  • WebSocket Gateway Server (JSON-RPC)                                │  │
│  │  • Session- & Prozessverwaltung, Subagent-Delegierung                │  │
│  │  • Support-Upload & Telemetrie (hermes support send-logs)             │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
└──────────────────────────────────────┼──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Agent Core & Tool-Engine                           │
│  ┌───────────────────────────┐         ┌─────────────────────────────────┐  │
│  │ agent/                    │         │ tools/                          │  │
│  │ • LLM Prompt Loop & State │         │ • MCP Client, Terminal, FS,    │  │
│  │ • Memory (Structured/FTS5)│         │   Vision, Process Registry,     │  │
│  │ • Reflexions- & Skill-Loop│         │   Approval System               │  │
│  └─────────────┬─────────────┘         └────────────────┬────────────────┘  │
│                │                                        │                   │
│  ┌─────────────┴─────────────┐         ┌────────────────┴────────────────┐  │
│  │ providers/                │         │ plugins/ & skills/              │  │
│  │ • LiteLLM / OpenAI /      │         │ • Erweiterungen (Web, Browser,  │  │
│  │   Azure / OpenRouter etc. │         │   Kanban, M365, Atlassian etc.) │  │
│  └───────────────────────────┘         └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detaillierte Ordnerübersicht

### A. Client-Anwendungen (`apps/`, `web/`, `ui-tui/`)
* **`apps/desktop/`**: Die offizielle **Desktop-App** (Electron + React 19 + Tailwind v4).
  * `electron/main.cjs`: Electron-Hauptprozess, Backend-Lebenszyklusverwaltung, IPC-Bridge, Support-Ticket-Upload (`hermes:support:reportIssue`).
  * `src/`: React-Frontend (Chat-Oberfläche, Session-Sidebar, Settings, Support-/Feedback-Dialog).
  * `build/`: Build-Ressourcen, native Node-Module (`node-pty`) und Install-Stamps.
* **`apps/bootstrap-installer/`**: Der plattformübergreifende **Setup-Installer & Updater** (Tauri v2 + Rust + React).
  * `src-tauri/`: Rust-Backend für die Installation von Python/Git/Hermes, Keycloak-SSO-Extraktion und direkten Support-Ticket-Upload zu `https://suite-support.iamds.com`.
  * `src/`: React-Benutzeroberfläche für Welcome-, Credentials- und Failure-Screens.
* **`apps/shared/`**: Gemeinsam genutzte UI-Komponenten und Hilfsfunktionen zwischen Desktop und Web.
* **`web/`**: Web-Dashboard (Vite + React), das vom Python-Backend statisch als interaktives Dashboard ausgeliefert werden kann.
* **`ui-tui/`**: Alternatives Terminal-basiertes UI auf Node.js/Ink-Basis.

---

### B. Core Agent & Runtime (`agent/`, `tools/`, `hermes_cli/`, `gateway/`, `tui_gateway/`)
* **`agent/`**: Das Herzstück der KI-Agentenlogik.
  * `agent.py` / `prompt_builder.py`: Ausführungsschleife (ReAct/Tool-Calling Loop) und Prompt-Zusammenstellung.
  * `memory.py` / `memory_dual_write.py`: Persistentes Langzeitgedächtnis (Dateisystem + SQLite FTS5).
  * `curator.py`: Hintergrund-Reflexion und automatische Skill-Optimierung.
* **`tools/`**: Werkzeuge, die der Agent autonom aufrufen kann.
  * `mcp_tool.py`: MCP-Client (Model Context Protocol) zur Anbindung externer Werkzeugserver.
  * `terminal_tool.py`, `file_tools.py`, `vision_tools.py`: Lokale Systeminteraktion.
  * `delegate_tool.py`: Starten von parallelen Subagenten.
  * `approval.py`: Sicherheits- und Freigabesystem für risikoreiche Befehle.
* **`hermes_cli/`**: CLI-Kommandos und Einstiegspunkte (`hermes chat`, `hermes support`, `hermes update` etc.).
  * `support_logs.py`: Export und Redaktion von Diagnoseprotokollen (`send-logs`, `send-telemetry`).
  * `main.py`: Zentraler CLI-Parser und Dispatcher.
* **`tui_gateway/` & `gateway/`**: WebSocket-Server und Messenger-Bridges (Slack, Teams, Telegram, Webhook) für Remote- und Desktop-Sessions.
* **`providers/`**: Modell- und Backend-Provider-Adapter (LiteLLM, Nous Portal, OpenRouter, Azure, Bedrock etc.).

---

### C. Skills & Plugins (`skills/`, `plugins/`, `optional-skills/`, `optional-mcps/`)
* **`skills/`**: Standard-Skills, die dem Agenten bei spezifischen Aufgaben Kontext und Workflows liefern (z.B. PDF-Parsing, Diagrammerstellung, Code-Refactoring).
* **`plugins/`**: Modular ladbare Erweiterungen (z.B. Suchprovider wie DuckDuckGo/Tavily, Browser-Automation, Achievements).
* **`optional-skills/`**: Große Sammlung spezialisierter Skills (z.B. MLOps, Blockchain, Pentesting), die bei Bedarf aktiviert werden können.
* **`optional-mcps/`**: Vorlagen und Konfigurationen für MCP-Server (z.B. Atlassian Jira/Confluence, Microsoft Office 365, GitHub).
* **`skills-disabled/`**: Temporär deaktivierte Upstream-Skills.

---

### D. Setup, Workspace-Templates & Packaging (`installer/`, `scripts/`, `packaging/`)
* **`installer/`**:
  * `workspace-template/`: Die initiale Arbeitsbereich-Vorlage mit Standard-Dokumenten und Verzeichnissen, die bei Erstinstallation in das Benutzerverzeichnis kopiert wird.
  * `scripts/`: Plattformspezifische Installationsskripte (`install.ps1`, `install-macos.sh`).
* **`scripts/`**: Entwickler- und CI-Skripte (`run_tests.sh`, `sign-windows.ps1`, `build_desktop.py` etc.).
* **`packaging/`**: Paketierungsbeschreibungen (z.B. Homebrew-Formel).

---

### E. Upstream-Forschungs- & Datengenerierungs-Tools (NousResearch)
*Hinweis: Diese Tools stammen aus dem Upstream-Repository für Synthetic Data Generation und Benchmarks:*
* **`batch_runner.py`**, **`mini_swe_runner.py`**, **`trajectory_compressor.py`**: Ausführung von SWE-bench-Aufgaben und Kompression von Agenten-Trajektorien.
* **`datagen-config-examples/`**: Konfigurationsbeispiele für Benchmark- und Trajektoriengenerierung.
* **`plans/`** & **`.plans/`**: Upstream-Konzeptpläne.

---

### F. Tests & Dokumentation (`tests/`, `docs/`)
* **`tests/`**: Umfassende Testsuite (Pytest) für Agent, Gateway, CLI, MCP-Tools und Support-Log-Uploads.
* **`docs/`**: Technische Dokumentationen, Spezifikationen und Benutzerhandbücher.
