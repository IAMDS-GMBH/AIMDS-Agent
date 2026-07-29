---
name: meeting-prep
description: Prepares comprehensive 2-minute meeting briefs combining calendar event details, attendee background, historical notes, open action items, and suggested discussion questions. Trigger when user asks "prepare for meeting", "meeting prep", "prep for [topic/person]", or before an upcoming meeting.
---

# Meeting Preparation Skill

This skill prepares concise, 2-minute executive meeting briefings by pulling together meeting details from calendar events, contact/person notes, past meeting minutes, and open action items.

## Workflow & Steps

1. **Fetch Event & Attendee Details**:
   - Query event information via `m365_list_calendar_events` or user input.
   - Extract subject, start/end times, organizer, attendees, and existing body notes or agendas.
   - Check real-time presence/status of attendees via `m365_get_user_presence`.

2. **Cross-Reference Past Notes & Open Tasks**:
   - Search local memory, knowledge base, or previous meeting notes for participant names or meeting topics.
   - Retrieve pending action items from `m365_list_todo_tasks` or task databases linked to attendees or projects.

3. **Construct Meeting Briefing**:

```markdown
# 📋 Meeting Prep: [Meeting Title / Topic]
**Date & Time:** [YYYY-MM-DD, HH:MM – HH:MM]
**Attendees:** [List of Names + Roles/Presence]

## 🔍 Context & Background
- [Bullet points summarizing prior discussions, recent email/chat exchanges, or project status]

## 🎯 Key Objectives & Proposed Agenda
1. [Agenda Point 1]
2. [Agenda Point 2]
3. [Agenda Point 3]

## ❓ Recommended Questions & Talking Points
- [Strategic question 1 to drive decision]
- [Strategic question 2]

## 📌 Open Action Items from Last Meeting
- [ ] [Pending action item 1]
- [ ] [Pending action item 2]
```

## Guidelines
- Format output clearly so the user can review it in under 2 minutes before jumping into the call.
- Link relevant projects, documents, or attendees using markdown links or references.
