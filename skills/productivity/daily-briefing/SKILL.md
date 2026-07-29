---
name: daily-briefing
description: Generates a daily executive briefing and morning overview covering today's calendar meetings, priority tasks, urgent inbox signals, and focus recommendations. Trigger when user asks "daily briefing", "morning overview", "what's on my schedule today", or "briefing".
---

# Daily Briefing & Morning Overview Skill

This skill acts as an executive Chief of Staff agent, compiling a comprehensive, actionable daily briefing for the user at the start of their day.

## Workflow & Data Sources

1. **Calendar & Schedule Lookup**:
   - Query today's meetings using `m365_list_calendar_events` or local calendar integrations.
   - Extract meeting start/end times (in local timezone `Europe/Berlin`), subjects, locations/Teams links, and attendees.
   - Check colleague free/busy availability if preparing for critical discussions (`m365_get_schedule`).

2. **Task & To Do Triage**:
   - Query pending tasks using `m365_list_todo_tasks` or local task/todo databases.
   - Highlight high-priority items, due/overdue deadlines, and open action items from previous days.

3. **Outlook Email & Teams Message Summary**:
   - Query today's emails via `m365_list_emails` / `m365_search_emails` or Outlook tools (`received >= today`).
   - Group emails by sender, urgency, and action required (e.g. decision requested, FYIs, client inquiries).
   - Scan recent unread Teams chat messages or call logs via `m365_list_messages` or `m365_list_teams_calls`.
   - Identify missed calls, direct mentions, or out-of-office status flags (`m365_get_mailbox_settings`).

4. **Briefing Output Format**:

```markdown
# 🌅 Daily Briefing — [Day, YYYY-MM-DD]

## 📅 Today's Schedule ([Total Meetings] Events)
- **09:00 – 09:30**: [Meeting Title] (with [Attendees]) — *[Key Context/Objective]*
- **11:00 – 12:00**: [Meeting Title] — 🔗 [Teams Link/Room]

## 📧 Today's Email Summary ([Count] Messages Received Today)
- **🔴 Action Required**:
  - **[Sender]** — *[Subject]*: [1-sentence summary of request/deadline]
- **🟡 Important Updates**:
  - **[Sender]** — *[Subject]*: [Summary]
- **🟢 FYIs & General**:
  - [Key takeaway]

## 🎯 Top Priority Action Items
1. 🔴 **[Urgent/Overdue Task]**: [Details & Deadline]
2. 🟡 **[Important Task]**: [Details]
3. 🟢 **[Follow-Up]**: [Details]

## 💬 Teams & Call Highlights
- **Teams Messages**: [Summary of key user mentions or chat updates today]
- **Calls**: [Missed calls or recent group meeting logs]

## 💡 Executive Summary & Focus Advice
- *[1-2 bullet summary on how to optimize time today, e.g. "Focus block recommended between 14:00 and 16:00 for report writing."]*
```

## Guidelines
- Keep time references in clean local time without offset clutter.
- Prioritize actionable clarity over raw lists. Highlight missing preparation or meeting overlaps.
