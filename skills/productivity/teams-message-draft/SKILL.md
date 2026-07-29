---
name: teams-message-draft
description: Drafts Teams messages based on user context or synthesizes messages/chats from a specific user. Trigger when user asks "create Teams message", "draft message for Teams", "what did user XY write today", or "summarize Teams chat".
---

# Teams Message Drafting & Chat Analysis Skill

This skill analyzes Teams chat history for specific users and drafts clean, HTML-formatted Teams messages based on previous conversation context or assistant answers.

## Workflows

### 1. Analyze User Messages ("What did User XY write today?")
- Query recent messages/chats via `m365_list_teams_calls`, `m365_list_messages`, or Teams MCP tools.
- Filter messages by sender name/email and timestamp (today's date).
- Summarize main requests, decision points, or questions raised by User XY.

### 2. Draft Teams Message from Context
- Convert previous assistant output or notes into a concise, professional Teams message.
- Structure message using clean HTML tags (`<p>`, `<strong>`, `<ul>`, `<li>`, `<br>`) as Teams renders HTML natively.
- Include a clear call-to-action or summary.

## Message Draft Format

```markdown
# 💬 Teams Message Draft for [Recipient / Channel]

**HTML Payload for `m365_send_chat_message`:**

```html
<p>Hallo [Name],</p>
<p>hier ist ein kurzes Update zu <strong>[Thema / Projekt]</strong>:</p>
<ul>
<li><strong>[Punkt 1]</strong>: [Kurze Details]</li>
<li><strong>[Punkt 2]</strong>: [Kurze Details]</li>
</ul>
<p>Lass mich wissen, falls du dazu Fragen hast!</p>
```
```

## Guidelines
- Always format Teams message bodies as HTML so formatting renders correctly in Microsoft Teams.
- Ensure greetings and register match the user's per-contact tone.
