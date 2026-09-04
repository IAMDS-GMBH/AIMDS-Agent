# Microsoft 365 MCP (MSOffice365MCP) — sign-in, consent tiers, tenant onboarding

The `MSOffice365MCP` catalog entry gives the agent Outlook mail and calendar, Teams,
OneDrive/SharePoint, contacts, presence and To Do through Microsoft Graph. Sign-in uses
MSAL's device-code flow against the **IAMDS-owned multi-tenant app registration**
(client ID `41c29967-8ee6-4fac-b484-e87460272bda`). Customers do not register an app;
they only approve ours in their tenant.

This page covers what a user can do alone, what needs a tenant administrator once, and
how to troubleshoot the Entra error codes you will meet on the way.

## Consent tiers

Microsoft classifies delegated Graph permissions by whether a tenant admin has to
approve them. The single source of truth for the lists is
`hermes_cli/m365_auth.py`; the MCP server mirrors them and a test pins equality.

| Tier | Constant | Scopes | Who can approve |
|---|---|---|---|
| 0 `self` | `M365_SELF_CONSENT_SCOPES` | User.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, Contacts.ReadWrite, Files.ReadWrite.All | Every user for themselves (default Entra policy) |
| 1 `standard` | `M365_ORG_CONSENT_SCOPES` (+ tier 0) | Mail.ReadWrite.Shared, Mail.Send.Shared, Calendars.ReadWrite.Shared, Chat.ReadWrite, Presence.Read, OnlineMeetings.Read, Tasks.ReadWrite | Tenant admin, once, org-wide |
| 2 `admin` | `M365_ADMIN_SCOPES` (+ tiers 0 and 1) | User.Read.All, Directory.Read.All, Sites.ReadWrite.All | Tenant admin, once, org-wide (or an admin signing in for themselves) |

**Every sign-in entry point requests tier 0 only**: the "Microsoft 365 (OAuth)" button in
Settings → Providers → Accounts, `hermes mcp install MSOffice365MCP`, and the
`m365_initiate_login` chat tool. That is what makes the flow work for non-admin users.
There is no second device-code fallback: if tier 0 is refused, the tenant blocks user
consent altogether and an admin has to onboard the tenant (next section).

Higher tiers arrive **silently**. MSAL refresh tokens are not scope-bound, so once an
administrator has consented org-wide, `acquire_token_silent` returns the wider token to
every already-signed-in user. The server probes `admin → standard → self` and caches the
granted tier per account for ten minutes, so a Graph call never pays failing network
round-trips more than once per window.

What the tiers unlock:

| Tool family | Needs |
|---|---|
| Outlook mail, calendar, contacts, OneDrive | tier 0 |
| Teams chats and channel messages, presence, online meetings, shared mailboxes/calendars, To Do | tier 1 |
| Directory-wide user search (`m365_search_users`), SharePoint sites | tier 2 |

## Tenant onboarding (what a customer admin does once)

1. In Hermes open Settings → Providers → Accounts → Microsoft 365 and click **Grant for
   organization**, or call `m365_generate_admin_consent_url` in chat, or fetch
   `GET /api/providers/oauth/microsoft/admin-consent-url` from the dashboard API.
   The URL has the form

   ```
   https://login.microsoftonline.com/<tenant-id|organizations>/v2.0/adminconsent
     ?client_id=41c29967-8ee6-4fac-b484-e87460272bda
     &scope=https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.ReadWrite …
     &redirect_uri=http://localhost:8400
   ```

   The tenant segment is the signed-in account's tenant when known, otherwise
   `organizations`. `common` is never used because the v2 admin-consent endpoint rejects it.
   `use_default_scope=true` sends `https://graph.microsoft.com/.default` instead, which
   grants exactly the permissions declared on the app registration.
2. A **Global Administrator, Application Administrator or Cloud Application
   Administrator** opens the link, signs in and clicks *Accept*. This creates the
   service principal in the tenant and records the org-wide grant.
3. The browser is redirected to `http://localhost:8400/?admin_consent=True&tenant=<id>`.
   Nothing listens there, so a "connection refused" page is **expected**. The consent
   already happened.
4. Verification without directory rights: in Hermes the Microsoft 365 card now reports
   *Organization approved* (`granted_tier` is `standard` or `admin`), because a normal
   user's silent token request for tier 1 succeeds. In Entra: Enterprise applications →
   *Hermes / AIMDS Agent* → Permissions shows the admin-consented Graph scopes.

Nobody has to sign in again. The next Graph call picks up the wider token.

### Prerequisites on the IAMDS app registration (IAMDS-side, once)

- Authentication → *Allow public client flows* = **Yes** (device code flow).
- Platform "Mobile and desktop applications" with `http://localhost` and
  `http://localhost:8400` as redirect URIs.
- Supported account types: multi-tenant (*Accounts in any organizational directory*).
- API permissions: all scopes of `M365_ALL_SCOPES` declared as delegated permissions.
  `.default` consent only grants what is declared here.
- **Publisher verification** (Microsoft Partner Network ID on the app). Under the Entra
  default user-consent policy ("allow user consent for apps from verified publishers, for
  selected permissions") users cannot even approve tier 0 for an unverified publisher and
  see *Need admin approval* immediately. Until the app is verified, the admin-consent
  step above is required for tier 0 as well.

## Sign-in entry points

| Path | Code | Scopes |
|---|---|---|
| Dashboard / Desktop button | `hermes_cli/web_server.py` `_start_device_code_flow("microsoft")` + `_microsoft_device_code_worker` | `M365_LOGIN_SCOPES` |
| CLI install | `hermes_cli/mcp_catalog.py` `_microsoft_device_code_login` | `M365_LOGIN_SCOPES` (a manifest may narrow, never widen) |
| Chat | `m365_initiate_login(scope_tier="self" \| "standard" \| "admin")` → `m365_complete_login(flow_data)` | tier of choice, default `self` |
| MCP server CLI | `python server.py --login [--standard \| --admin]` | tier of choice |

`m365_complete_login` reports `granted_tier`; when it is `self` the response also carries
`admin_consent_hint.admin_consent_url` to hand to an administrator. A Graph `403` on a
tier 1 or tier 2 endpoint is rewritten into the same hint, so the agent can tell the user
which consent step is missing instead of guessing.

Optional overrides: `M365_CLIENT_ID` / `M365_TENANT_ID` (also `OUTLOOK_*` / `TEAMS_*`)
switch to a customer-owned app registration. The token cache lives in
`~/.hermes/m365_token_cache.bin` and is shared by CLI, dashboard and MCP server.

## Troubleshooting Entra errors

`hermes_cli.m365_auth.classify_m365_auth_error()` turns MSAL results into a category and a
plain-English message; the dashboard poll endpoint exposes it as `error_code`,
`error_category` and, for consent failures, `action_url` (the admin-consent URL).

| Code | Category | Meaning / fix |
|---|---|---|
| AADSTS65001, AADSTS90094 ("Need admin approval"), AADSTS500011 | consent | The tenant has not approved the app for this permission set. Run the tenant onboarding above. |
| AADSTS65002 | consent | Scope is not exposed to third-party apps; remove it from the request. |
| `consent_required`, `interaction_required` | consent | Same as above, reported as OAuth error strings by MSAL. |
| AADSTS50076 | mfa | Conditional access / MFA required. Complete MFA in the browser. |
| AADSTS7000218, `unauthorized_client` | config | Public client flows are disabled on the app registration. |
| AADSTS700016, `invalid_client` | config | Wrong client ID for this tenant (check `M365_CLIENT_ID`). |
| AADSTS650052 | config | The tenant has not enabled a service the app depends on. |
| AADSTS70016, `authorization_pending`, `expired_token` | pending / expired | The device code was not entered in time. Start again. |
| `authorization_declined`, `access_denied` | declined | The user cancelled or a policy denied the sign-in. |
| `invalid_grant` | expired | Cached session no longer valid (password change, revoked). Sign in again. |

## Teams: sending to a person without guessing

`m365_send_chat_message(to=<name | nickname | email | topic>, content=<Markdown>)` resolves
the chat through `m365_find_chat`, which ranks the signed-in user's own chats by member
email / name / nickname stem / group topic (no directory permission needed). It sends only
on a `unique` resolution; `ambiguous` returns the candidates and the agent has to ask;
`none` points to `m365_get_or_create_direct_chat`, which itself prefers an existing 1:1
chat and only falls back to the directory (admin tier) or the raw UPN. Markdown in
`content` is rendered to the HTML Teams displays, so the preview shown in chat equals the
message that arrives; `rendered_html` and `plain_text` in the result let the agent confirm
what went out. `dry_run=true` resolves and renders without sending.

`m365_get_chat_style(to=…)` derives the register the user actually uses with that person
(language, du/Sie, greeting, sign-off, length, emoji, formality) from their own recent
messages in the chat; the agent stores it as a `person` memory note ("Teams style with
<Name>") and drafts in that register. Without history the Teams defaults apply: short,
no letter salutation, no closing formula, no signature, no attribution line, no
implementation detail. `m365_list_chats`, `m365_list_chat_messages` and
`m365_get_chat_members` return compact records by default (`raw=true` for Graph objects).

A pasted Teams deep link (`https://teams.microsoft.com/l/chat/<chatId>/0?…` or
`/l/message/<chatId>/<messageId>?…`) is accepted wherever a chat id is expected
(`chat_id`, `to`, `query`). `m365_download_chat_files(chat_id=<link>|to=<name>, last=5,
include_images=false)` scans the last messages for shared files, downloads them into the
Vault under `documents/m365_attachments/<chat>/` and returns the saved paths with sender and
time. `m365_download_email_attachments(message_id)` does the same for all file attachments of a
mail (`documents/m365_attachments/mail/<subject>/`). Sending works the other way round: pass
Vault-relative or absolute paths in `attachments=[…]` of `m365_send_chat_message` (OneDrive
upload + file card) or `m365_send_email` (inline, ≤ 3 MB). Catalog installs also pick up new
manifest `default_enabled` tools automatically: the
runtime unions `mcp_servers.<name>.tools.include` with the manifest defaults, and a reinstall
adds them to the prior selection (AIS-288).

`m365_download_drive_file(file_id)` also takes a SharePoint/OneDrive URL (a file's webUrl, a
sharing link, or the `contentUrl` of a chat attachment) and resolves it through the Graph
shares API; the default target is `documents/m365_downloads/` in the Vault. A Teams attachment
id is not a drive item id. In Hermes a Teams link or SharePoint URL in the user's message loads
the matching tools before the first model call (`agent/deferred_tools.autoload_for_message`),
and the Teams guidance block is built from the reachable tools even while they are deferred
behind `tool_search` (AIS-289). MCP results are shaped before the model sees them — see
`mcp_results` in `cli-config.yaml.example`.

## Related

- The Messaging *Outlook* connector (`tools/microsoft_graph_auth.py`) is a **different**
  integration with its own customer app registration and client secret; see
  [../messaging/outlook-setup.md](../messaging/outlook-setup.md). Do not mix its scope list
  with the MCP tiers above.
- Support-case history behind this design: SUP-20260902-123535, SUP-20260903-074039
  (Jira AIS-286).
