---
type: project
title: ""
projectStatus: active        # active | waiting | dormant | done | parked
created: ""
updated: ""
due: ""                       # may be empty, never omitted
due-reason: ""                # required when due is empty ("ongoing", "waiting for customer")
related_to: []
tags: []
---

# {{title}}

**Goal:** <what "done" looks like>

**Status note:** <one line — why active/waiting/etc.>

## Next steps
- [ ] 

## Log
<!-- newest first — the agent appends progress here -->

<!--
projectStatus rules (the agent proposes changes, never moves silently):
  active   — being worked on
  waiting  — blocked on someone/something external (surfaced as a reminder only)
  dormant  — paused, comeback unclear
  done     — finished
  parked   — not started, backlog
-->
