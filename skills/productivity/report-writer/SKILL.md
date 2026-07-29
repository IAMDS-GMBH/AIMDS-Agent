---
name: report-writer
description: Transforms notes, data, git activity, project logs, or research into polished executive summaries, status reports, or client updates. Trigger when user asks "write a report", "executive summary", "status update", "weekly report", or "create client summary".
---

# Executive Report & Summary Writer Skill

This skill converts raw notes, data points, repository activity, or research findings into structured, executive-ready reports, status updates, or formal document drafts.

## Workflow & Inputs

1. **Gather Input Data**:
   - Collect raw inputs (meeting notes, git commit logs, project milestones, research findings, or email threads).
   - Identify target audience: Internal Management, Technical Team, or External Client.

2. **Structure the Report**:
   - Apply standard executive reporting structure:
     - **Executive Summary**: 2–3 high-level takeaway sentences for decision makers.
     - **Key Accomplishments / Milestones**: What was achieved during the period.
     - **Current Status & Metrics**: Quantitative progress, key performance indicators, or build/test health.
     - **Risks, Blockers & Mitigations**: Any technical or organizational impediments.
     - **Next Steps & Roadmap**: Clear upcoming deliverables with deadlines and owners.

3. **Report Template**:

```markdown
# 📊 Executive Report: [Project / Subject Title]
**Date:** [YYYY-MM-DD] | **Author:** [Author / Hermes Agent] | **Scope:** [Status / Milestone]

## 💡 Executive Summary
[High-level summary of progress, impact, and strategic alignment in 2–3 sentences.]

## 🚀 Key Accomplishments
- **[Area/Milestone 1]**: [Details on completed deliverable and business value]
- **[Area/Milestone 2]**: [Details]

## 📈 Status & Key Metrics
| Metric / Component | Status | Target / Progress | Notes |
|---|---|---|---|
| [System Component A] | 🟢 Operational | 100% | [Comment] |
| [Feature Deliverable B] | 🟡 In Progress | 80% | [Estimated completion] |

## ⚠️ Risks & Blockers
- **[Risk/Blocker 1]**: [Description, impact level, and proposed mitigation]

## 🎯 Next Steps & Milestones
- [ ] **[Deliverable 1]** — Owner: [Name], Target: [YYYY-MM-DD]
- [ ] **[Deliverable 2]** — Owner: [Name], Target: [YYYY-MM-DD]
```

## Formatting Guidelines
- Maintain professional, objective, and executive tone.
- When exporting to Word (.docx) or Excel (.xlsx), apply guidelines from the `office-formatting` skill.
