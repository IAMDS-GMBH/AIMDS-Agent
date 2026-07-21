---
name: outlook-tool
description: How to actually use the outlook_* tools (mail, calendar, contacts) — including finding them via tool_search when they are not directly listed. Use whenever the user asks about their Outlook/Microsoft 365 mailbox, calendar, or contacts, or if you catch yourself about to say "check your inbox" instead of checking it.
---

# Outlook Tool Usage

You have DIRECT, LIVE access to the user's Outlook / Microsoft 365 mailbox,
calendar, and contacts through the `outlook_*` tools. Never tell the user to
"check Outlook themselves", "run this later", or "let you know when a reply
arrives" — call the tool yourself, right now, and report the real result.

## Step 0 — find the tools if they are not directly listed

These tools live in the `outlook` toolset. On some models/sessions they are
NOT shown directly in your system prompt (progressive tool disclosure /
`tool_search` is active). If you do not see `outlook_get_emails`,
`outlook_search_emails`, `outlook_write_email`, etc. in your available tools,
do **not** conclude Outlook is unavailable and do **not** fabricate an answer.
Instead:

1. Call `tool_search` with a query like `"outlook email"`, `"outlook
   calendar"`, or `"outlook contacts"`.
2. Call `tool_describe` on the matching tool name to load its full schema.
3. Call `tool_call` with that tool name + arguments to actually run it.

This 3-step bridge (`tool_search` → `tool_describe` → `tool_call`) behaves
exactly like calling the tool directly — same guardrails, same approvals,
same result. Skipping it and just guessing/refusing is the wrong answer.

## Step 1 — pick the right tool

| Task | Tool |
|---|---|
| Sign in / check if connected | `outlook_authenticate` |
| Quick overview ("what did I get today") | `outlook_get_emails` |
| Find an email by topic/sender/recipient/date | `outlook_search_emails` |
| Read the full body of ONE known email | `outlook_read_email` (needs `message_id`) |
| Read a shared/delegate mailbox (not the user's own) | `outlook_read_shared_mail` |
| Send / reply / forward | `outlook_write_email` |
| Read upcoming meetings | `outlook_read_calendar_entries` |
| Create / change / cancel a meeting | `outlook_write_calendar_entries` |
| Read contacts (personal or org directory) | `outlook_read_contacts` |
| Create / change / delete a contact | `outlook_write_contacts` |

Do not use `outlook_get_emails` to search by keyword (use
`outlook_search_emails`), and do not stuff sender/date filters as free text
into `query` — they never match verbatim and always return zero results. Use
the dedicated `sender`/`recipient`/`date_from`/`date_to` parameters.

## Step 2 — respect the two-step confirm contract

`outlook_write_email` and `outlook_write_calendar_entries` (update/delete)
never act on the first call. The first call (no `confirm`, or
`confirm=false`) only returns a preview + a `draft_id`/current state. You
MUST show that preview to the user and only call again with `confirm=true`
once they explicitly approve. When confirming `outlook_write_email`, pass
`confirm=true` together with the SAME `draft_id` — do not retype
to/subject/body, that has previously caused retry loops that silently never
sent the email.

## Verification
- You called a tool instead of telling the user to check manually.
- Preview shown and approved before any `confirm=true` send/update/delete.
- Search filters used structured params, not free-text guesses.

## What NOT to do
- No constructing your own OAuth/device-code/Graph HTTP requests — use
  `outlook_authenticate`.
- No `confirm=true` on the first call, ever.
- No giving up because the tool "isn't in the list" — use `tool_search` first.
