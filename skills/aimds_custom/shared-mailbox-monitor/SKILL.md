---
name: shared-mailbox-monitor
description: Configures or executes recurring cron checks for Microsoft 365 shared mailboxes (Shared Postfach) that the user is authorized for. Use for "check shared mailbox", "monitor support@domain.com", or "create cron job for shared mailbox".
---

# Shared Mailbox Monitor

## Procedure
1. **Identify Shared Mailbox:**
   - Determine the shared mailbox email address (e.g., `support@company.com`, `vertrieb@company.com`, `info@company.com`).
   - Check if MSOffice365MCP / `email-triage` tools or `msoffice365_list_messages` can access the shared mailbox (using `mailbox` or `--user-id` parameter).

2. **Configure Cron Job:**
   - When asked to monitor or create a recurring check for a shared mailbox, create a Hermes cron job using the cron tool or `cron/jobs.json`.
   - **Recommended Schedule:** Every 1–2 hours during work hours (`0 */2 * * 1-5` or `0 * * * 1-5`).
   - **Prompt Contract (Token-Efficient):**
     "Check shared mailbox <shared_mailbox_address> for new unread/actionable customer emails. If there are no new unread or actionable emails, report 'nothing new'. If new actionable customer emails arrive, summarize them compactly in the user's language, extract key customer requests/to-dos, and alert the user."
   - Set skill to `digest` or `email-triage`.

3. **Check & Triage Execution:**
   - When running the check, read unread messages from the shared mailbox.
   - Filter out automated notifications, OOF replies, and spam.
   - Summarize customer requests concisely (1 line per email).
   - If action is required (e.g. quote request, customer issue), highlight required actions and prepare draft responses as drafts only.

## Guardrails
- **Never auto-send emails.** Always present drafts for human approval.
- Keep output silent or brief ("nothing new") when no new customer messages are present to preserve tokens.
