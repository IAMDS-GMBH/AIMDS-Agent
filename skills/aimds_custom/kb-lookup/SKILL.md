---
name: kb-lookup
description: Answers company-knowledge questions (policies, processes, templates, internal facts) from the AIMDS-AI-KB and cites sources. Use for "what is our process for...", "where can I find...", "what is our policy on...".
---

# KB Lookup

## Procedure
1. **Clarify the question** (1 sentence).
2. **Query the KB:** use `kb_search` for discovery, `kb_get_topic` for the full article,
   and `kb_get_related` for related topics.
3. **Answer with citations:** claim + source (title/topic). If the KB has no answer,
   state that **clearly** — do not guess.

## Verification
- The answer comes from the KB, not from model memory.
- The source is named and actually exists.

## What NOT to do
- Never "recall" company knowledge from memory — always check the KB.
- Do not cite outdated information if a newer topic exists.
