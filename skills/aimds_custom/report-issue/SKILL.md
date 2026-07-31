---
name: report-issue
description: Assists the user with reporting a problem, submitting feedback, sending diagnostic logs, or proposing a feature improvement. Trigger when the user says "problem melden", "fehler melden", "verbesserungsvorschlag", "feedback geben", "support log senden", or asks how to report an issue or suggest an improvement.
---

# Report Issue & Feedback

## Overview
Guides and assists users with reporting bugs, submitting support logs, or proposing feature improvements/suggestions directly to the AIMDS Support team.

## Procedure
1. **Identify Request Type:**
   - **Improvement / Feature Suggestion:** Collect summary, motivation, and proposed behavior. Use category `feature_request`.
   - **Bug / Technical Error:** Collect what failed, error message, steps to reproduce, and ask if diagnostic logs or session context should be attached.
2. **Support Options in Hermes:**
   - **In Hermes Desktop UI:** User can click the **Problem melden / Feedback** icon (speech bubble icon) in the top titlebar (next to Settings), or use Settings -> Support.
   - **Automated Support Log Export:** If using CLI or when requested, run `hermes support send-logs` to upload diagnostic logs directly to support server.
3. **Formulate the Report:**
   - Help the user write a structured summary and user description.
   - Set category (`chat_issue`, `mcp_tools`, `feature_request`, `ui_bug`, `llm_timeout`, `connection_error`, `performance`, `installation_update`, `other`) and severity (`low`, `medium`, `high`, `critical`).
4. **Track Status:**
   - Give the user their ticket reference ID and explain that status can be tracked under Settings -> Support Tickets.

## Key Categories
- `chat_issue`: Chat & Antworten (Problem im Chat / KI antwortet nicht)
- `mcp_tools`: MCP & Tools (Werkzeug oder Server nicht gefunden / fehlerhaft)
- `feature_request`: Verbesserungsvorschlag & Idee
- `ui_bug`: Benutzeroberfläche & Anzeige
- `llm_timeout`: KI-Verbindung & Timeout
- `connection_error`: Gateway/Netzwerk-Verbindung
- `performance`: Performance & Tempo
- `installation_update`: Installation & Update
- `other`: Sonstiges
