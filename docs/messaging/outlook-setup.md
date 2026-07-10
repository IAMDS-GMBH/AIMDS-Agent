# Microsoft Outlook Messaging — Setup Guide

This guide explains how to connect Hermes to Microsoft Outlook via the Microsoft Graph API using
delegated permissions. By default, sign-in uses a one-click **browser-based OAuth flow** (similar
to "Sign in with Microsoft" in OWA) — Hermes opens your browser, you sign in, and a local loopback
redirect completes the flow automatically, with no code to copy. Hermes automatically falls back to
the classic **device code flow** (open a URL, type a short code) on hosts where a local browser
can't be reached, e.g. headless/remote gateway servers — and you can also force either flow
explicitly if you prefer. Once configured, you can interact with Outlook directly from the Hermes
chat — reading mail, sending messages, and working with your calendar.

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
   entry works for every sign-in — no need to register a fixed port. This is only required for the
   new browser-based (loopback) flow; the device code fallback does not use a redirect URI.
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
| `Calendar.Read` | Read calendar events |
| `Calendar.ReadBasic` | Read basic calendar metadata |
| `Calendar.ReadWrite` | Create and update calendar events |
| `offline_access` | Maintain access (refresh tokens) without re-authentication |
| `User.Read` | Read the signed-in user's profile |

> **Note:** Do **not** add application permissions (app-only). All of the above must be added as **Delegated** permissions.

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
# Optional — defaults to "auto" (browser sign-in, falls back to device code)
OUTLOOK_INTERACTIVE_AUTH_FLOW=auto
```

`OUTLOOK_INTERACTIVE_AUTH_FLOW` accepts:
- `auto` (default) — try the browser-based loopback flow first; fall back to device code
  automatically if a local listener can't be started (e.g. no local browser reachable, such as a
  headless/remote gateway host).
- `loopback` — force the browser-based flow; fails with a clear error instead of silently falling
  back, useful to confirm the new flow is actually being used.
- `device_code` — force the classic manual-code flow (recommended for remote/headless gateway
  hosts where a loopback listener can never work).

---

## Step 7 — Authenticate

### Option A — Via chat

Once the Tenant ID and Client ID are saved, you can ask Hermes in the chat:

> *"Authenticate Outlook"* or *"Connect my Outlook account"*

Hermes will open a Microsoft sign-in link for you — no code to enter — and detect the successful
authentication automatically. On hosts where the browser-based flow isn't available, it falls back
to a short device code + verification URL instead, and instructs the model on exactly how to
continue; Hermes never invents its own manual sign-in steps.

### Option B — Via the Start Auth button

1. In the Hermes desktop app, go to **Messaging** → **Outlook**.
2. Click the **Start Auth** button.
3. A dialog will appear:
   - Browser sign-in (default): click **"Open Microsoft Login"** and complete sign-in in your
     browser — Hermes detects completion automatically, no code needed.
   - Device code (fallback or forced): click **"Open Microsoft Login"**, then copy the short code
     shown and paste it when prompted in the browser.
4. Once signed in, click **Test Connection** to confirm Hermes can reach your mailbox. Test
   Connection performs a direct Microsoft Graph check and works independently of whether the
   gateway process is currently running.

### Sending emails

When you ask Hermes to send an email, it always shows a preview (To/CC/Subject/Body) first and
waits for your explicit confirmation before actually sending — it will never send an email
speculatively.

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
| `AADSTS50020: User account … does not exist in tenant` | Wrong tenant ID, or user is in a different tenant | Verify the Tenant ID matches the user's Azure AD tenant |
| `AADSTS65001: The user or administrator has not consented` | Admin consent not granted | Complete Step 3 |
| Browser sign-in falls back to a device code unexpectedly | No local browser reachable, or the loopback listener couldn't bind (headless/remote host) | Expected behavior — complete the device code flow, or set `OUTLOOK_INTERACTIVE_AUTH_FLOW=device_code` to make this the default and avoid the fallback attempt |
| Sign-in link or device code expires before authentication completes | Browser session too slow | Click **Start Auth** again to start a fresh sign-in |
| `Mail.Send` succeeds but emails go to Junk | SPF/DKIM not set for Graph-sent mail | Contact your IT team to allowlist Graph API outbound mail |
| "Gateway is not running" shown on the Messaging page | Gateway process stopped | Click the **Restart Gateway** button next to the message |

