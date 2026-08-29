---
name: vector-memory-search
description: Token-effiziente 3-Stufen-Suche über AIMDSSuiteMCP, Qdrant und den lokalen Vektor-Index, um Prompt-Tokens um bis zu 90% zu reduzieren.
metadata:
  hermes:
    requires_tools: [memory_search]
---

# Vector & Memory Search (Token-Optimiert)

## Zweck
Verhindert das Laden vollständiger Dokumente in den Prompt-Kontext. Erzwingt ein schrittweises Abfrage-Verfahren, um Antworten präzise und extrem sparsam im Token-Verbrauch zu generieren.

## Das 3-Stufen-Abfragemodell

```
[Stufe 1: Index & Themen] ──> [Stufe 2: BM25/Vektor-Suche] ──> [Stufe 3: Chunk-Reading]
  storage_status()              storage_search(query)             storage_get_document(
  storage_meta("topics")        memory_search(query)                id, offset_words=...)
  (~50-150 Tokens)              (~150-300 Tokens)                 (~200-500 Tokens)
```

### Stufe 1: Status & Themen-Überblick (Sehr sparsam)
1. Aufrufen von `storage_status()` zur Prüfung des Index-Zustands.
2. Bei allgemeinen Themenfragen: `storage_meta({"kind":"topics","limit":10})`.

### Stufe 2: Zielgerichtete Suche (Gibt nur IDs & 1-Zeilen-Exzerpte zurück)
1. Ausführen von `storage_search({"query":"provider:upload type:pdf <Suchbegriffe>","limit":5})` oder `memory_search({"query":"<Suchbegriffe>"})`.
2. Analysiere die zurückgegebenen IDs, Dokumentennamen und Kurz-Exzerpte.

### Stufe 3: Selektives Chunk-Reading (Liest nur benötigte Wörter)
1. Lade NIE das gesamte Dokument, wenn eine spezifische Frage beantwortet werden soll.
2. Nutze Chunk-Offseting:
   ```json
   storage_get_document({
     "id": "google_drive:1abcDEF",
     "offset_words": 0,
     "chunk_size_words": 400
   })
   ```

## Token-Einsparung & Best Practices
- **Keine Re-Injections:** Dokumente oder Memory-Ergebnisse nicht erneut im Prompt spiegeln.
- **Provider-Filter nutzen:** Operatoren wie `provider:upload`, `provider:gdrive`, `type:pdf` oder `tag:<term>` beschleunigen Suchen in Qdrant und `AIMDSSuiteMCP`.
