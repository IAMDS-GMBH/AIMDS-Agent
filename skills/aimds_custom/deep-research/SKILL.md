---
name: deep-research
description: Researches a concrete question across multiple sources, cross-checks claims, and delivers a concise, cited synthesis. Use when the user asks to "research", "find out", or requests market/competitor/topic intelligence.
---

# Deep Research

## Procedure
1. **Scope (max 1 sentence):** What exactly, for what purpose, and at what depth? Ask briefly if unclear.
2. **Collect sources:** at least 2-3 independent sources. First check **AIMDS-AI-KB**
   (`kb_search`) for internal knowledge, then the web.
3. **Cross-check:** do ≥2 sources agree? Is the date current? Marketing vs. neutral?
   If sources conflict, present both positions.
4. **Synthesize:** findings first, evidence second. Cite every claim (link/title).
5. **Confidence:** explicitly state high / medium / low. Never hide `low`.

## Verification (before "done")
- Every key claim has a real, named source (no invented URLs).
- Open gaps are stated explicitly ("no verifiable statement on X found").

## What NOT to do
- No hallucinated citations. Do not use a single marketing source as evidence.
- No outdated sources without a date warning.
