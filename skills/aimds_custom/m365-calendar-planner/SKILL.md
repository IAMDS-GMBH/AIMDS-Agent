---
name: m365-calendar-planner
description: Queries Microsoft 365 Outlook calendar events, finds meeting slots, checks conflicts, and prepares meeting context from emails and customer CRM data.
metadata:
  hermes:
    requires_toolsets: [MSOffice365MCP]
---

# M365 Calendar Planner

## Purpose & procedure
1. **Query the calendar:** Fetch appointments with `m365_get_events`.
2. **Identify conflicts:** Check overlaps and preparation windows.
3. **Prepare meeting context:**
   - Search relevant emails via `m365_list_emails(query=...)`.
   - Search customer information in `go-mcp-customer` via `storage_search(query=...)`.
4. **Store the preparation note:** Save meeting briefings directly at `~/Documents/AIMDS-Suite-Vault/meetings/YYYY-MM-DD-Meeting-<Topic>.md`.
