# Outlook messaging connector — setup

This page is about the **Messaging → Outlook** connector (`tools/microsoft_graph_auth.py`),
which polls a mailbox and routes mail into the agent like a chat channel. It uses a
customer-owned Entra app registration with a client secret. It is **not** the Microsoft 365
MCP: that one signs the user in interactively through the IAMDS multi-tenant app and is
documented in [../integrations/microsoft-365-mcp.md](../integrations/microsoft-365-mcp.md).

## App registration (customer tenant)

1. Entra admin center → App registrations → *New registration*. Single tenant is fine.
2. Certificates & secrets → create a client secret; note the value.
3. API permissions → *Microsoft Graph → Delegated*, add exactly the scopes the connector
   requests (see `DEFAULT_DELEGATED_SCOPE` in `tools/microsoft_graph_auth.py`):

   ```
   Mail.Read Mail.Send Mail.Read.Shared Calendars.ReadWrite
   Contacts.Read Contacts.ReadWrite OrgContact.Read.All User.Read offline_access
   ```

   `Mail.ReadWrite` is deliberately not requested. Requesting a scope that is not granted
   on the registration fails at sign-in with `AADSTS65001` / `invalid_scope`.
4. *Grant admin consent* for the tenant (`OrgContact.Read.All` and `Mail.Read.Shared`
   require it).

## Hermes configuration

Set the connector's environment variables (Settings → Messaging → Outlook, or `~/.hermes/.env`):

| Variable | Value |
|---|---|
| `OUTLOOK_CLIENT_ID` | Application (client) ID |
| `OUTLOOK_TENANT_ID` | Directory (tenant) ID |
| `OUTLOOK_CLIENT_SECRET` | The secret from step 2 |

Then connect the account from the Messaging page; the connector runs the OAuth
authorization-code flow and stores refresh tokens in the Hermes home directory.

## Troubleshooting

The AADSTS table in the Microsoft 365 MCP page applies here as well. The most common
cause of `AADSTS65001` on this connector is a missing admin consent for one of the shared
or org-wide scopes above.
