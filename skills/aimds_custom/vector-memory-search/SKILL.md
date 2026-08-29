---
name: vector-memory-search
description: Token-efficient 3-stage search across AIMDSSuiteMCP, Qdrant and the local vector index (storage_status, storage_search, memory_search, storage_get_document chunk reading) to cut prompt tokens by up to 90%.
metadata:
  hermes:
    requires_tools: [memory_search]
---

# Vector & Memory Search (Token-Optimised)

## Purpose
Prevents loading complete documents into the prompt context. Enforces a step-by-step query procedure to produce precise answers with extremely low token consumption.

## The 3-Stage Query Model

```
[Stage 1: Index & Topics] ──> [Stage 2: BM25/Vector Search] ──> [Stage 3: Chunk Reading]
  storage_status()              storage_search(query)             storage_get_document(
  storage_meta("topics")        memory_search(query)                id, offset_words=...)
  (~50-150 tokens)              (~150-300 tokens)                 (~200-500 tokens)
```

### Stage 1: Status & topic overview (very cheap)
1. Call `storage_status()` to check the index state.
2. For general topic questions: `storage_meta({"kind":"topics","limit":10})`.

### Stage 2: Targeted search (returns only IDs & one-line excerpts)
1. Run `storage_search({"query":"provider:upload type:pdf <search terms>","limit":5})` or `memory_search({"query":"<search terms>"})`.
2. Analyse the returned IDs, document names and short excerpts.

### Stage 3: Selective chunk reading (reads only the words needed)
1. NEVER load the entire document when a specific question is to be answered.
2. Use chunk offsetting:
   ```json
   storage_get_document({
     "id": "google_drive:1abcDEF",
     "offset_words": 0,
     "chunk_size_words": 400
   })
   ```

## Token savings & best practices
- **No re-injections:** Do not mirror documents or memory results back into the prompt.
- **Use provider filters:** Operators such as `provider:upload`, `provider:gdrive`, `type:pdf` or `tag:<term>` speed up searches in Qdrant and `AIMDSSuiteMCP`.
