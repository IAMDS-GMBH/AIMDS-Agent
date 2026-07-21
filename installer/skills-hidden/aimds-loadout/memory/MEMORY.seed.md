# MEMORY.seed — Startwerte für Agent-Notizen (lokal, Schicht A)

> Nur maschinen-/setup-spezifische Fakten. KEIN Firmenwissen (→ KB), keine
> portablen User-Fakten (→ User-Memory-MCP). Limit: 2.200 Zeichen.

- Firmenwissen wird über das AIMDS-AI-KB abgefragt (`kb_search`), nicht aus dem Gedächtnis.
- Durable User-Fakten gehören in den zentralen User-Memory-MCP, nicht hierher.
- <maschinenspezifisch: z.B. Outlook-Profil, Standard-Ablageordner>
