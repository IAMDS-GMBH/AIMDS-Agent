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
- `chat_issue`: chat & answers (problem in the chat / the AI does not answer)
- `mcp_tools`: MCP & tools (tool or server not found / faulty)
- `feature_request`: improvement suggestion & idea
- `ui_bug`: user interface & display
- `llm_timeout`: AI connection & timeout
- `connection_error`: gateway / network connection
- `performance`: performance & speed
- `installation_update`: installation & update
- `other`: other
