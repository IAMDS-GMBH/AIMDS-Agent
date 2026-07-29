---
name: weekly-sync
description: Summarizes weekly progress across repositories, commits, completed tasks, meetings, and upcoming goals for weekly team syncs or status reviews. Trigger when user asks "weekly sync", "weekly summary", "what did I do this week", or "weekly status".
---

# Weekly Sync & Activity Overview Skill

This skill compiles a comprehensive weekly progress review by aggregating git commits, completed M365 tasks, meeting history, and upcoming milestones.

## Data Sources & Aggregation

1. **Git Commit & PR History**:
   - Query git log for the past 7 days (`git log --since="7 days ago" --oneline`).
   - Group commits by feature, bug fix, or project scope.

2. **M365 & Task Completion**:
   - Check completed tasks in Microsoft To Do (`m365_list_todo_tasks`).
   - Query weekly meeting logs and call history (`m365_list_calendar_events`, `m365_list_teams_calls`).

3. **Weekly Sync Output Format**:

```markdown
# 🔄 Weekly Sync Overview ([Start Date] – [End Date])

## ✅ Key Highlights & Deliverables
- **[Feature / Project A]**: [Summary of key accomplishment and impact]
- **[Feature / Project B]**: [Summary]

## 💻 Repository & Code Activity
- **Commits**: [X] commits merged across [Y] branches/repos
- **Key Changes**:
  - `[type]([scope])`: [Brief commit description]
  - `[type]([scope])`: [Brief commit description]

## 📞 Key Discussions & Call Log
- **Meetings**: [Summarize main decision points from calendar events or Teams calls]

## 🎯 Plan for Next Week
1. **[Goal 1]**: [Planned work and milestone]
2. **[Goal 2]**: [Planned work]
```

## Guidelines
- Focus on business impact and progress over raw commit counts.
- Keep summaries concise and suitable for sharing in weekly team status meetings or email updates.
