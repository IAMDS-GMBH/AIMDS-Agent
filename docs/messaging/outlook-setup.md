# Microsoft Outlook Messaging — Setup Guide

This guide explains how to connect Hermes to Microsoft Outlook via the Microsoft Graph API using
delegated permissions. By default, sign-in uses a one-click **browser-based OAuth flow** (similar
to "Sign in with Microsoft" in OWA) — Hermes opens your browser, you sign in, and a local loopback
redirect completes the flow automatically, with no code to copy. A single sign-in covers mail
(read/write/shared), calendar, and contacts — you won't be asked to sign in again when switching
between tools. The classic **device code flow** (open a URL, type a short code) is kept as a legacy
option for hosts where a local browser can't be reached (e.g. headless/remote gateway servers), but
it is only ever used when explicitly selected — Hermes never switches to it automatically. Once
configured, you can interact with Outlook directly from the Hermes chat — reading mail, sending
messages, working with your calendar, and managing contacts.

---

## Overview

| Who does this step | What |
|---|---|
| App developer / IT admin | Register the Azure AD app and configure permissions |
| Azure AD admin | Grant admin consent |
| Azure AD admin | Allow public client flows + add a loopback redirect URI |
| App developer / IT admin | Share Tenant ID + Client ID with Hermes users |
| Hermes user | Enter Tenant ID + Client ID in the Outlook messaging section |
| Hermes user | Sign in via the **Start Auth** button (or a chat command) |

---

## Step 1 — Register an Azure AD Application

1. Go to the [Azure portal](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
2. Give the app a name (e.g. `Hermes Agent`).
3. Set **Supported account types** to **Accounts in this organizational directory only** (single tenant) — or multi-tenant if needed.
4. Under **Redirect URI**, choose platform **Mobile and desktop applications** and add:
   ```
   http://localhost
   ```
   Microsoft matches this against any local loopback port at runtime, so a single `http://localhost`
   entry works for every sign-in — no need to register a fixed port. **This step is required even if
   you only ever plan to use the device code flow** — Azure AD returns `AADSTS500113: No reply
   address is registered for the application` for *both* the browser sign-in and device code flows
   if the app registration has no redirect URI registered at all.
5. Click **Register**.

After registration, note down:
- **Application (client) ID** — shown on the app overview page.
- **Directory (tenant) ID** — also on the app overview page.

---

## Step 2 — Add Required API Permissions

In your app registration, go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**.

Add the following permissions:

| Permission | Purpose |
|---|---|
| `Mail.Read` | Read emails from the mailbox |
| `Mail.Send` | Send emails on behalf of the user |
| `Mail.Read.Shared` | Read shared/delegated mailboxes |
| `Calendars.ReadWrite` | Read, create, and update calendar events |
| `Contacts.Read` | Read Outlook contacts |
| `Contacts.ReadWrite` | Create, update, and delete Outlook contacts |
| `OrgContact.Read.All` | Read organizational contacts |
| `User.Read` | Read the signed-in user's profile |
| `offline_access` | Maintain access (refresh tokens) without re-authentication |

> **Note:** Do **not** add application permissions (app-only). All of the above must be added as **Delegated** permissions.
> **Note:** `Mail.ReadWrite` is intentionally **not** requested — sending only needs `Mail.Send`, and none of the current tools edit/flag existing messages.

---

## Step 3 — Grant Admin Consent

An Azure AD administrator must grant consent for these permissions so users do not need to approve each one individually.

In **API permissions**, click **Grant admin consent for \<your tenant\>** and confirm.

The status column should show **Granted for \<your tenant\>** (green checkmark) for each permission.

---

## Step 4 — Allow Public Client Flows

Both the browser-based loopback flow and the device code fallback are OAuth 2.0 public client flows
and must be explicitly allowed by Azure AD.

### Option A — Enable for the entire tenant (recommended for internal tools)

1. Go to **Azure Active Directory** → **Enterprise applications** → find your registered app.
2. Under **Properties**, ensure **User assignment required** is configured as appropriate for your org.
3. Go to **Azure Active Directory** → **Authentication methods** or check your [Conditional Access](https://portal.azure.com/#view/Microsoft_AAD_IAM/ConditionalAccessBlade) policies to confirm public client / device code flows are not blocked.
4. In your **App registration** → **Authentication**, enable **Allow public client flows** (under **Advanced settings**). Set **Enable the following mobile and desktop flows** to **Yes**.

> This is the key toggle — without it, both the loopback and device code flows will be rejected.

### Option B — Enable per user via Conditional Access

If your organisation restricts auth flows via Conditional Access, work with your Azure AD admin to create a policy that allows public client flows for the specific users or groups that will use Hermes.

---

## Step 5 — Share Credentials with Hermes Users

The person who registered the app must share two values with each Hermes user:

- **Tenant ID** — the Directory (tenant) ID from the app overview
- **Client ID** — the Application (client) ID from the app overview

These do **not** need to be kept secret (unlike a client secret). They identify the app registration, not any individual user.

---

## Step 6 — Configure Hermes

### Desktop app

1. Open Hermes → **Messaging** section in the left sidebar.
2. Select **Outlook** from the platform list.
3. Enter the **Tenant ID** and **Client ID** provided by your app developer/admin.
4. Click **Save changes**.

<p align="center">
  <img src="../../assets/desktop-messaging-outlook.png" alt="Outlook messaging configuration panel" width="85%">
</p>

Optionally, expand **Advanced** to set the **Sign-in method** — leave it on **Automatic
(recommended)** unless you have a specific reason to force **Browser sign-in** or **Device code
(legacy / headless)**.

### CLI / `.env`

Alternatively, add to your `.env` file:

```env
OUTLOOK_TENANT_ID=<your-tenant-id>
OUTLOOK_CLIENT_ID=<your-client-id>
# Optional — defaults to "auto" (always browser sign-in; reuses cached auth first)
OUTLOOK_INTERACTIVE_AUTH_FLOW=auto
```

`OUTLOOK_INTERACTIVE_AUTH_FLOW` accepts:
- `auto` (default) — always uses the browser-based loopback flow. Reuses any existing valid
  token/refresh token first, and only opens a new browser sign-in when nothing usable is cached
  (or on the rare occasion Microsoft rejects the refresh outright). Never silently switches to
  device code.
- `loopback` — same as `auto` today; kept as an explicit, pinned alias.
- `device_code` — force the classic manual-code flow. Only use this if this specific host cannot
  bind a local loopback listener (e.g. a remote/headless gateway host with no local browser) —
  device code is legacy and is never started automatically as a fallback from the other two modes.

---

## Step 7 — Authenticate

### Option A — Via chat

Once the Tenant ID and Client ID are saved, you can ask Hermes in the chat:

> *"Authenticate Outlook"* or *"Connect my Outlook account"*

Hermes will open a Microsoft sign-in link for you — no code to enter — and detect the successful
authentication automatically. Device code sign-in is legacy and only runs if you've explicitly
selected it as the Sign-in method; Hermes never invents its own manual sign-in steps or silently
switches flows on you. This whole flow is handled by a single dedicated `outlook_authenticate`
tool — the model never needs to (and is instructed not to) construct a manual OAuth/device-code
request itself, or use a read/write Outlook tool just to trigger a sign-in prompt. You can also
just ask *"is Outlook connected?"* to have Hermes confirm your current sign-in still works.

### Option B — Via the Start Auth button

1. In the Hermes desktop app, go to **Messaging** → **Outlook**.
2. Click the **Start Auth** button.
3. A dialog will appear:
   - Browser sign-in (default): click **"Open Microsoft Login"** and complete sign-in in your
     browser — Hermes detects completion automatically, no code needed.
   - Device code (legacy, only shown if explicitly selected as the Sign-in method): click **"Open
     Microsoft Login"**, then copy the short code shown and paste it when prompted in the browser.
4. Once signed in, click **Test Connection** to confirm Hermes can reach your mailbox. Test
   Connection performs a direct Microsoft Graph check and works independently of whether the
   gateway process is currently running.

### One sign-in covers everything

All Outlook tools (read/write mail, shared mailbox, calendar, contacts) request the same combined
set of permissions and share the same cached refresh token — signing in once is enough for all of
them. You should not be asked to sign in again just because you switched from reading email to
writing a calendar entry or looking up a contact, for example. If you are, check the Troubleshooting
table below.

### Sending emails

When you ask Hermes to send an email, it always shows a preview (To/CC/Subject/Body) first and
waits for your explicit confirmation before actually sending — it will never send an email
speculatively. After sending, Hermes double-checks that the message actually landed in your Sent
Items folder (Microsoft Graph's send API only confirms the request was accepted, not that delivery
succeeded) — if that check doesn't find it within a few seconds, Hermes will tell you it couldn't
be verified instead of claiming the email was definitely sent.

### Managing contacts

Hermes can read/search your Outlook contacts and create, update, or delete them. Just as with
calendar and email changes, updating or deleting a contact always shows a preview of the current
contact and the requested change first — nothing is modified until you explicitly confirm.

### Persistent sign-in

You only need to sign in once. A successful sign-in (either flow) stores a refresh token, and
Microsoft Entra ID keeps renewing it on a rolling basis as long as it's used at least once within
its validity window (roughly every 90 days by default) — so Hermes stays signed in across restarts
and tool calls without prompting you again.

---

## Restarting the gateway

If the Messaging page shows **"Gateway is not running"**, click the **Restart Gateway** button next
to that message to restart it without leaving the page.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AADSTS7000218: The request body must contain the following parameter: 'client_assertion' or 'client_secret'` | Public client flows not enabled | Enable **Allow public client flows** in the app's Authentication settings (Step 4) |
| `AADSTS500113: No reply address is registered for the application` (shown on Microsoft's own sign-in page, for *either* browser sign-in or device code) | App registration has no redirect URI at all | Add `http://localhost` under **Mobile and desktop applications** in the app's Authentication settings (Step 1) — required even if you only use the device code flow |
| `AADSTS50020: User account … does not exist in tenant` | Wrong tenant ID, or user is in a different tenant | Verify the Tenant ID matches the user's Azure AD tenant |
| `AADSTS65001: The user or administrator has not consented` | Admin consent not granted | Complete Step 3 |
| `AADSTS70011: The provided value for the input parameter 'scope' is not valid` (`invalid_scope`) | Requested Graph permission isn't added to the app registration | Make sure all permissions listed in Step 2 are added (and admin-consented) — this can happen after removing/renaming a permission on the app registration without redeploying Hermes |
| Sending an email or replying reports an error even though the message went through / appears in Sent Items | Microsoft Graph's `sendMail`/`reply` endpoints reply with `202 Accepted` and an empty body — older Hermes builds tried to parse that empty body as JSON and raised a false error | Fixed — update to the latest Hermes build; if it still happens, please report it as a bug |
| Browser sign-in falls back to a device code unexpectedly | This should no longer happen — device code is never used as an automatic fallback | If you still see this, please report it as a bug |
| Repeatedly asked to sign in again for different Outlook actions (mail vs. calendar vs. shared mailbox vs. contacts) | Fixed — all Outlook tools now share one combined-scope sign-in | If it still happens, check `~/.hermes/outlook_token.json` exists and is writable, and that only one Hermes profile/gateway is in use |
| Sign-in link or device code expires before authentication completes | Browser session too slow, or you clicked **Start Auth** again before finishing the previous attempt (that reuses the same still-pending link now instead of starting a new one) | Complete the sign-in shown, or click **Start Auth** again once the previous link has actually expired |
| `Mail.Send` succeeds but emails go to Junk | SPF/DKIM not set for Graph-sent mail | Contact your IT team to allowlist Graph API outbound mail |
| Sending an email fails (e.g. `ErrorAccessDenied` / `Access is denied`) even though sign-in works | `Mail.Send` permission missing from the app registration (`Mail.ReadWrite` alone does **not** grant sending) | Add `Mail.Send` under **API permissions** (Step 2), then click **Grant admin consent** again |
| Hermes reports the email was sent, but it's missing from your Sent folder | The send request was accepted by Graph but never actually verified in Sent Items (stale token, delayed sync, or a genuine delivery failure) | Hermes now checks Sent Items after sending and reports `sent_unverified` with a warning if it can't confirm — if you see a plain "sent" but still can't find it, wait a minute and refresh Sent Items, then re-check permissions (row above) |
| "Gateway is not running" shown on the Messaging page | Gateway process stopped | Click the **Restart Gateway** button next to the message |

