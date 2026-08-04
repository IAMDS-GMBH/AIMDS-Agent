---
type: harness
title: "HARNESS — operating principles"
status: reference
updated: ""
---
# HARNESS — how the agent works (beyond filing)

> AGENTS.md is the **map** (where things go, always loaded). This file is the
> **method** (how to work well) — consulted for any non-trivial task.
> Ported from the AIMDS reference vault (patrick-brain BRAIN-HARNESS, v 2026-08-04),
> which proved these rules in production. Keep customer-specific additions at the end.

## 0. Principles

1. **Prime directive: reduce the user's involvement.** Before proposing anything,
   ask: does this reduce the user's workload — or add to it? If it adds, it is the
   wrong proposal. Prefer: decide once, anchor as a rule — never re-present the same
   decision weekly.
2. **Goal-first.** Every non-trivial task gets a checkable definition of done
   *before* work starts. No "feels finished".
3. **Verify against the source, never self-assess.** Numbers against the table,
   claims against the document. Anti-hallucination is a workflow, not a hope.
4. **State lives in files, not in chat.** Anything reusable gets filed.
5. **Index-first.** To find something, read the folder's `_hub.md` first, then
   search. Structural navigation beats blind scanning.
6. **Cheapest sufficient model** per task; escalation is handled by the LiteLLM
   proxy — do the work, don't manage routing.
7. **Learn from corrections.** After any user correction, immediately record the
   rule (here or in AGENTS.md) — not just in chat memory.
8. **Finding → fix, not finding → finding.** Reporting the same issue twice without
   a fix proposal is a failure mode.

## 1. Metadata schema — closed vocabulary

Only these values. A new value must be added HERE first, then used.

- `type`: `project` · `knowledge` · `idea` · `decision` · `contact` · `hub` ·
  `journal` · `task-list` · `automation` · `archive`
- `status` (notes): `raw` · `active` · `waiting` · `done` · `parked` · `reference`
- `projectStatus` (projects only): `active` · `waiting` · `dormant` · `done` · `parked`
- Projects also carry `due:` (may be empty — never omitted) and, when empty,
  `due-reason:` ("ongoing until ~2028", "waiting for customer decision") so an empty
  date is distinguishable from a forgotten one.

**Why closed:** with a free vocabulary the agent cannot filter ("all open decisions")
— the reference vault had 51 type variants before enforcement and none of its agents
could query reliably.

## 2. This workspace is NOT a project-management tool

Project files hold **context** plus **the user's own open points**. Team tasks live
in the team's PM tool (Jira, customer systems). Consequences:

- **Never** infer from few tasks that a project is stalled — the work runs elsewhere.
- A project file with **0 open points is a success**, not a finding: nothing hangs
  on the user.
- A project file that **swells** with team tasks is the real alarm — work is leaking
  into the wrong place.

## 3. Decay and rotation — knowledge must age visibly

| What | Rule |
|---|---|
| `status: raw` older than 90 days | flag for triage: curate or archive |
| Logs older than 60 days | rotate to an `_archive/<year>/` folder |
| `updated:` on every content change | always — otherwise age is unmeasurable |
| Hub counts and links | re-check periodically (health-check pattern) |

Never delete — archive. `_archive/` folders are never edited retroactively.

## 4. No dashboards without an update mechanism

Build a status view only if something keeps it current automatically. A dashboard
that can go stale is worse than none — it creates false confidence. Prefer
regeneratable output (fresh per session/briefing) over persisted views.

## 5. Escalation — things must not silently age

When surfacing open items to the user, escalate by age without movement:
> 14 days: mention once · > 30 days: top of the list with age ·
> 60 days: force a decision — "drop it, or this week?"

Items delegated to a person get a `delegated-to:` marker in the source file so they
are not re-proposed daily.

## 6. Stop conditions (ask, don't guess)

- Important information missing → question into `_open-questions.md`, keep working
  on the rest.
- Anything irreversible (delete, external send, money) → explicit user confirmation.
- Two failed attempts at the same thing → stop, reanalyze, ask.

---

## ⚙ Customer-specific additions

> Add rules learned from working with THIS user/customer below. Keep each to 1-3
> lines with the date. When one contradicts a generic rule above, the specific one
> wins — note the override explicitly.

(none yet)
