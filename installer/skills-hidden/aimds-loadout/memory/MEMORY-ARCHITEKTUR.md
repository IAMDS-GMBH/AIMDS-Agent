# Memory-Architektur — AIMDS Hermes Loadout

> Wie "Gedächtnis" funktioniert. Wichtigste Design-Entscheidung des Loadouts, weil
> Hermes' eingebautes Memory winzig ist — die echte Persistenz liegt zentral über
> **LiteLLM `/memory`** (PostgreSQL, user- und team-scoped).

## Backend: LiteLLM `/memory`
Zentrales Gedächtnis = LiteLLMs `/v1/memory`-CRUD-Endpoint:
- **PostgreSQL-backed**, Key-Value mit JSON-Metadaten.
- **Namespaced Keys**: `user:{id}:preferences`, `team:{id}:playbook`, …
- **Scoping & Zugriffskontrolle eingebaut**: Einträge sind an `user_id` und
  `team_id` gebunden, abgeleitet aus dem **API-Key des Callers**. Ein normaler
  User sieht nur eigene + Team-Einträge. → **Mandantentrennung ist damit gelöst.**
- CRUD: `POST/GET/PUT(upsert)/DELETE /v1/memory`, Prefix-Filter, Audit-Trail
  (`created_by`/`updated_by`).

⚠ **Eigenschaft beachten:** Das ist **Key-Value, keine semantische Suche.** Abruf
erfolgt über Key/Prefix, nicht über Bedeutung. Heißt: strukturierte, deterministische
Keys nutzen. Für bedeutungsbasierten Recall → KB / Vector Stores (Schicht D).

## Das Problem mit Hermes-Standard-Memory
Hermes' eingebautes Memory ist bewusst klein und lokal:
- `MEMORY.md` (Agent-Notizen): **2.200 Zeichen** · `USER.md` (Profil): **1.375 Zeichen**
- Liegt unter `~/.hermes/memories/` auf **diesem einen Gerät**
- **Eingefrorener Snapshot** beim Session-Start (Änderungen wirken erst nächste Session)

Gut für "wer ist der User auf dieser Maschine" — nicht für firmenweites,
gerätübergreifendes Gedächtnis. Dafür LiteLLM `/memory`.

## Die 4 Schichten

| Schicht | Was | Backend | Reichweite | Wahrheit für |
|---|---|---|---|---|
| **A — Hermes lokal** | Maschinen-/Sessionfakten, schneller Cache | `~/.hermes/memories/` | dieses Gerät | nichts Portables |
| **B — User-Memory** | durable Profil, Präferenzen, Kontakte | LiteLLM `/memory` `user:{id}:*` | **alle Geräte des Users** | **alles Nutzerbezogene** |
| **C — Team-Memory** | geteilte Playbooks, Team-Konventionen, Glossar | LiteLLM `/memory` `team:{id}:*` | **ganzes Team** | **geteiltes Team-Wissen** |
| **D — AIMDS-AI-KB** | Firmenwissen: Policies, Prozesse, Templates | LiteLLM MCP Gateway | ganze Firma | **kuratiertes Firmenwissen** |

```
   Hermes (Desktop) ──► LiteLLM (zentrales Gateway)
        │                 ├─ Model-Endpoint (Inference + Goal-Judge)
        │                 ├─ /memory ──► PostgreSQL   (B user:* / C team:*)
        │                 └─ MCP Gateway ──► AIMDS-AI-KB  (D, semantisch)
        └─ lokal: MEMORY.md / USER.md  (A, schneller Cache)
```

**B/C vs. D — die Abgrenzung:** B/C ist **veränderliches, scoped Gedächtnis**
(was der Agent über User/Team lernt). D ist **kuratiertes, read-only Firmenwissen**
mit semantischer Suche. Nicht vermischen.

## Die Regeln

### Was wohin gehört
- **Firmenwissen** (Prozess, Policy, Template) → **D (KB)**, gelesen, nie gespeichert.
- **Durable Nutzer-Fakten** (Rolle, Präferenzen, Stammkontakte) → **B** (`user:{id}:*`).
- **Geteiltes Team-Wissen** (Playbook, Konventionen, Standard-Antworten) → **C**
  (`team:{id}:*`). Nur Team-Admins schreiben, alle Team-Mitglieder lesen.
- **Maschinen-/Sessionspezifisches** → **A** lokal.

### Lesen (read)
1. Nutzerkontext nötig → **B** abfragen (`GET /memory/user:{id}:…`), live & aktuell —
   nicht auf den eingefrorenen lokalen Snapshot verlassen.
2. Team-Vorgehen nötig → **C** (`team:{id}:playbook`).
3. Firmenwissen → **D** (`kb_search`), mit Quellenangabe.
4. Schicht A wird automatisch in den Prompt geladen.

### Schreiben (write)
1. Durable Nutzer-Fakt gelernt → **B** (`PUT /memory/user:{id}:…`, upsert).
   Optional knapp zusätzlich in lokales `USER.md` (A) als schneller Cache.
2. Team-Playbook ändern → **C**, aber nur durch Team-Admins (LiteLLM erzwingt das).
3. Firmenwissen schreibt der End-User-Agent **nicht** (D wird kuratiert gepflegt).
4. **Autorität bei Konflikt: B/C (zentral) gewinnt über A (lokaler Cache).**

### Session-Verlauf vs. durable Memory
- Was in einer früheren Session gesagt/entschieden wurde, wird über
  **`session_search`** aus der Transcript-DB geholt.
- Durable Memory (B/C) speichert stabile Fakten (Profil, Präferenzen,
  Team-Konventionen) — nicht laufenden Task-Status.

### Write-Gates & Auto-Capture
- `memory.write_approval`:
  - `false` (Default) → Writes laufen direkt durch.
  - `true` → Writes werden zur Freigabe gestaged (`/memory pending` etc.).
- `skills.write_approval` analog für Skill-Änderungen.
- `memory.managed_memory.capture_mode`:
  - `off` → kein automatisches Erfassen
  - `suggest` → Agent schlägt Speicherung vor
  - `auto` → Agent speichert geeignete durable Fakten proaktiv

### Keys (Konvention)
```
user:{user_id}:profile        # Rolle, Stil, Präferenzen
user:{user_id}:contacts       # wiederkehrende Kontakte/Konten
team:{team_id}:playbook       # Team-Vorgehen, Standards
team:{team_id}:glossary       # Begriffe, Abkürzungen
```

## Anbindung an Hermes
Hermes spricht nativ MCP; LiteLLM `/memory` ist REST. Drei Wege (vor Pilot wählen):
1. **MCP-Wrapper** — dünner MCP-Server, der `memory_get/list/upsert/delete` auf
   `/v1/memory` mappt. Passt am besten zu eurem MCP-Gateway-Setup, transparent.
2. **Hermes Memory Provider** — Plugin, das gegen den Endpoint läuft (neben built-in).
3. **Direkter REST-Call** als Tool. Am simpelsten, am wenigsten integriert.
→ Empfehlung: **MCP-Wrapper** (1).

## Enterprise-Schutz
- **Zugriffskontrolle** kommt aus LiteLLM (user/team-Scope per Key) — kein
  Cross-User-Leak.
- **Hermes lokal:** `memory.write_approval: true` + `skills.write_approval: true`,
  wenn Freigabe-gesteuerte Writes gewünscht sind (z.B. Enterprise-Policy).
- **Audit:** `/memory` führt `created_by`/`updated_by` mit Timestamps.

## Warum die Trennung A/B/C/D
- **A** ist in jedem Prompt sofort da (0 Tool-Call) → die 2-3 Always-on-Fakten.
- **B/C** sind portabel, scoped, unbegrenzt, governt → ein Tool-Call wert.
- **D** ist semantisch durchsuchbar und firmenweit kuratiert.
- Kurzform: **A = schneller Cache, B = ich, C = mein Team, D = die Firma.**

## Offene Punkte
- ⚠ Anbindungs-Weg (MCP-Wrapper vs. Provider vs. REST) final entscheiden.
- ⚠ Key-Value ≠ semantisch: für "finde passende Erinnerung nach Bedeutung"
  ggf. Vector Stores kombinieren — oder bewusst bei strukturierten Keys bleiben.
- ⚠ `team_id`-Vergabe sauber mappen (welcher User in welchem LiteLLM-Team).
