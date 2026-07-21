import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { StatusDot, type StatusTone } from '@/components/status-dot'
import { Button } from '@/components/ui/button'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  getMessagingPlatforms,
  type MessagingEnvVarInfo,
  type MessagingPlatformInfo,
  testOutlookConnection,
  updateMessagingPlatform
} from '@/hermes'
import { type Translations, useI18n } from '@/i18n'
import { AlertTriangle, ExternalLink, Save, Trash2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { PageSearchShell } from '../page-search-shell'
import { CREDENTIAL_CONTROL_CLASS } from '../settings/credential-key-ui'
import { ListRow } from '../settings/primitives'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

import { PlatformAvatar } from './platform-icon'
import { OutlookAuthModal } from './outlook-auth-modal'
import { OutlookSetupGuideModal } from './outlook-setup-guide-modal'

interface MessagingViewProps extends React.ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

type EditMap = Record<string, Record<string, string>>

const PILL_TONE: Record<StatusTone, string> = {
  good: 'bg-primary/10 text-primary',
  muted: 'bg-muted text-muted-foreground',
  warn: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  bad: 'bg-destructive/10 text-destructive'
}

const OUTLOOK_PLATFORM_ID = 'outlook'

const trimEdits = (edits: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(edits)
      .map(([k, v]) => [k, v.trim()])
      .filter(([, v]) => v)
  )

const FIELD_COPY: Record<string, { advanced?: boolean }> = {
  TELEGRAM_PROXY: { advanced: true },
  DISCORD_REPLY_TO_MODE: { advanced: true },
  DISCORD_ALLOW_ALL_USERS: { advanced: true },
  DISCORD_HOME_CHANNEL: { advanced: true },
  DISCORD_HOME_CHANNEL_NAME: { advanced: true },
  BLUEBUBBLES_ALLOW_ALL_USERS: { advanced: true },
  MATTERMOST_ALLOW_ALL_USERS: { advanced: true },
  MATTERMOST_HOME_CHANNEL: { advanced: true },
  QQ_ALLOW_ALL_USERS: { advanced: true },
  QQBOT_HOME_CHANNEL: { advanced: true },
  QQBOT_HOME_CHANNEL_NAME: { advanced: true },
  WHATSAPP_ENABLED: { advanced: true },
  WHATSAPP_MODE: { advanced: true }
}
const OUTLOOK_VISIBLE_KEYS = new Set(['OUTLOOK_TENANT_ID', 'OUTLOOK_CLIENT_ID'])

function supportsMessagingToolset(platform: MessagingPlatformInfo): boolean {
  return platform.id === OUTLOOK_PLATFORM_ID
}

function hasConfiguredOutlookCredentials(platform: MessagingPlatformInfo, edits?: Record<string, string>): boolean {
  if (platform.id !== OUTLOOK_PLATFORM_ID) {
    return false
  }

  const typedTenant = (edits?.OUTLOOK_TENANT_ID || '').trim()
  const typedClient = (edits?.OUTLOOK_CLIENT_ID || '').trim()

  if (typedTenant && typedClient) {
    return true
  }

  return (
    platform.env_vars.some(field => field.key === 'OUTLOOK_TENANT_ID' && field.is_set) &&
    platform.env_vars.some(field => field.key === 'OUTLOOK_CLIENT_ID' && field.is_set)
  )
}

function messagingBadge(
  platform: MessagingPlatformInfo,
  m: Translations['messaging'],
  options: {
    edits?: Record<string, string>
    outlookConnected?: boolean | null
  } = {}
): {
  label: string
  tone: StatusTone
} {
  if (!supportsMessagingToolset(platform)) {
    return {
      label: m.disabled,
      tone: 'muted'
    }
  }

  const authenticated = options.outlookConnected === true || platform.state === 'connected'
  const effectiveAuthenticated = authenticated || platform.auth_ready === true

  if (effectiveAuthenticated) {
    return {
      label: m.states.connected,
      tone: 'good'
    }
  }

  if (hasConfiguredOutlookCredentials(platform, options.edits)) {
    return {
      label: m.credentialsSet,
      tone: 'warn'
    }
  }

  return {
    label: m.needsSetup,
    tone: 'muted'
  }
}

function fieldCopy(field: MessagingEnvVarInfo, m: Translations['messaging']) {
  const copy = FIELD_COPY[field.key] || {}
  const localized = m.fieldCopy[field.key] || {}

  return {
    label: localized.label || field.prompt || field.key,
    help: localized.help || field.description,
    placeholder: localized.placeholder || field.prompt,
    advanced: Boolean(copy.advanced || field.advanced)
  }
}

export function MessagingView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: MessagingViewProps) {
  const { t } = useI18n()
  const m = t.messaging
  const [platforms, setPlatforms] = useState<MessagingPlatformInfo[] | null>(null)
  const [edits, setEdits] = useState<EditMap>({})
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const [outlookAuthOpen, setOutlookAuthOpen] = useState(false)
  const [outlookGuideOpen, setOutlookGuideOpen] = useState(false)
  const [outlookConnected, setOutlookConnected] = useState<boolean | null>(null)
  const platformIds = useMemo(() => platforms?.map(p => p.id) ?? [], [platforms])
  const [selectedId, setSelectedId] = useRouteEnumParam('platform', platformIds, platformIds[0] ?? '')

  const refreshPlatforms = useCallback(async (silent = false) => {
    if (!silent) {
      setRefreshing(true)
    }

    try {
      const result = await getMessagingPlatforms()
      setPlatforms(result.platforms)
    } catch (err) {
      if (!silent) {
        notifyError(err, m.loadFailed)
      }
    } finally {
      if (!silent) {
        setRefreshing(false)
      }
    }
  }, [m])

  useRefreshHotkey(() => void refreshPlatforms())

  useEffect(() => {
    void refreshPlatforms()
  }, [refreshPlatforms])

  // Auto-poll while the user is on the messaging page so connection status
  // updates without a manual "check" click. Pause when the tab is hidden.
  useEffect(() => {
    let cancelled = false

    function tick() {
      if (cancelled || document.hidden) {
        return
      }

      void refreshPlatforms(true)
    }

    const id = window.setInterval(tick, 6000)

    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [refreshPlatforms])

  const selected = useMemo(() => {
    if (!platforms) {
      return null
    }

    return platforms.find(platform => platform.id === selectedId) || platforms[0] || null
  }, [platforms, selectedId])

  const outlookTenantEdit = (selected ? edits[selected.id]?.OUTLOOK_TENANT_ID : '') || ''
  const outlookClientEdit = (selected ? edits[selected.id]?.OUTLOOK_CLIENT_ID : '') || ''
  const outlookSecretEdit = (selected ? edits[selected.id]?.OUTLOOK_CLIENT_SECRET : '') || ''
  const outlookHasAnyTypedCred = Boolean(outlookTenantEdit.trim() || outlookClientEdit.trim())
  const outlookHasAllTypedCreds = Boolean(
    outlookTenantEdit.trim() && outlookClientEdit.trim()
  )

  useEffect(() => {
    setOutlookConnected(null)
  }, [selectedId, outlookTenantEdit, outlookClientEdit])

  const visiblePlatforms = useMemo(() => {
    if (!platforms) {
      return []
    }

    const q = query.trim().toLowerCase()

    if (!q) {
      return platforms
    }

    return platforms.filter(platform =>
      [platform.id, platform.name, platform.description, platform.state]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(q))
    )
  }, [platforms, query])

  async function handleSave(platform: MessagingPlatformInfo) {
    const env = trimEdits(edits[platform.id] || {})

    if (Object.keys(env).length === 0) {
      return
    }

    setSaving(`env:${platform.id}`)

    try {
      await updateMessagingPlatform(platform.id, { env })
      setEdits(current => ({ ...current, [platform.id]: {} }))
      await refreshPlatforms()
      notify({
        kind: 'success',
        title: m.setupSaved(platform.name),
        message: m.restartToReconnect
      })
    } catch (err) {
      notifyError(err, m.failedSave(platform.name))
    } finally {
      setSaving(null)
    }
  }

  async function handleClear(platform: MessagingPlatformInfo, key: string) {
    setSaving(`clear:${key}`)

    try {
      await updateMessagingPlatform(platform.id, { clear_env: [key] })
      setEdits(current => ({
        ...current,
        [platform.id]: {
          ...(current[platform.id] || {}),
          [key]: ''
        }
      }))
      await refreshPlatforms()
      notify({ kind: 'success', title: m.keyCleared(key), message: m.setupUpdated(platform.name) })
    } catch (err) {
      notifyError(err, m.failedClear(key))
    } finally {
      setSaving(null)
    }
  }

  async function handleToggleEnabled(platform: MessagingPlatformInfo, enabled: boolean) {
    setSaving(`enabled:${platform.id}`)

    try {
      await updateMessagingPlatform(platform.id, { enabled })
      await refreshPlatforms()
      notify({
        kind: 'success',
        title: enabled ? m.platformEnabled(platform.name) : m.platformDisabled(platform.name),
        message: m.restartToApply
      })
    } catch (err) {
      notifyError(err, m.failedUpdate(platform.name))
    } finally {
      setSaving(null)
    }
  }

  async function handleTestOutlookConnection(platform: MessagingPlatformInfo) {
    setSaving(`connection:${platform.id}`)

    try {
      const result = await testOutlookConnection()
      setOutlookConnected(result.ok)
      notify({
        kind: result.ok ? 'success' : 'warning',
        title: result.ok ? `${platform.name} connected` : `${platform.name} connection test`,
        message: result.message
      })
    } catch (err) {
      notifyError(err, `${platform.name} connection test failed`)
    } finally {
      setSaving(null)
    }
  }

  return (
    <PageSearchShell
      {...props}
      onSearchChange={setQuery}
      searchHidden={(platforms?.length ?? 0) === 0}
      searchPlaceholder={m.search}
      searchValue={query}
    >
      {!platforms ? (
        <PageLoader label={m.loading} />
      ) : (
        <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[14rem_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-y-auto p-2">
            <ul className="space-y-1">
              {visiblePlatforms.map(platform => (
                <li key={platform.id}>
                  <PlatformRow
                    active={selected?.id === platform.id}
                    onSelect={() => setSelectedId(platform.id)}
                    platform={platform}
                  />
                </li>
              ))}
            </ul>
          </aside>

          <main className="min-h-0 overflow-hidden">
            {selected && (
              <>
                <PlatformDetail
                  edits={edits[selected.id] || {}}
                  onClear={key => void handleClear(selected, key)}
                  onEdit={(key, value) =>
                    setEdits(current => ({
                      ...current,
                      [selected.id]: {
                        ...(current[selected.id] || {}),
                        [key]: value
                      }
                    }))
                  }
                  onSave={() => void handleSave(selected)}
                  onToggleEnabled={enabled => void handleToggleEnabled(selected, enabled)}
                  platform={selected}
                  saving={saving}
                  onOutlookOpenGuide={() => setOutlookGuideOpen(true)}
                  onOutlookTest={() => setOutlookAuthOpen(true)}
                  onOutlookTestConnection={() => void handleTestOutlookConnection(selected)}
                  outlookConnected={selected.state === 'connected' ? true : outlookConnected}
                />
                {selected.id === 'outlook' && (
                  <OutlookSetupGuideModal open={outlookGuideOpen} onClose={() => setOutlookGuideOpen(false)} />
                )}
                {selected.id === 'outlook' && (
                  <OutlookAuthModal
                    open={outlookAuthOpen}
                    tenantId={outlookTenantEdit}
                    clientId={outlookClientEdit}
                    clientSecret={outlookSecretEdit}
                    useSavedEnv={!outlookHasAnyTypedCred}
                    onComplete={async _accessToken => {
                      // Sign-in only — connection verification is a separate,
                      // explicit "Test Connection" step, not run automatically here.
                      setOutlookConnected(true)
                      setOutlookAuthOpen(false)
                      notify({
                        kind: 'success',
                        title: `Signed in to ${selected.name}`,
                        message: 'Use "Test Connection" to verify it works.'
                      })
                      await refreshPlatforms()
                    }}
                    onCancel={() => setOutlookAuthOpen(false)}
                  />
                )}
              </>
            )}
          </main>
        </div>
      )}
    </PageSearchShell>
  )
}

function PlatformRow({
  active,
  onSelect,
  platform
}: {
  active: boolean
  onSelect: () => void
  platform: MessagingPlatformInfo
}) {
  const { t } = useI18n()
  const badge = messagingBadge(platform, t.messaging)

  return (
    <button
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
        active
          ? 'bg-(--ui-row-active-background) text-foreground'
          : 'text-(--ui-text-secondary) hover:bg-(--ui-row-hover-background) hover:text-foreground'
      )}
      onClick={onSelect}
      type="button"
    >
      <PlatformAvatar platformId={platform.id} platformName={platform.name} />
      <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
        <span className="truncate text-[length:var(--conversation-text-font-size)] font-normal">{platform.name}</span>
        <StatusDot tone={badge.tone} />
      </span>
    </button>
  )
}

function PlatformDetail({
  edits,
  onClear,
  onEdit,
  onSave,
  onToggleEnabled,
  platform,
  saving,
  onOutlookOpenGuide,
  onOutlookTest,
  onOutlookTestConnection,
  outlookConnected
}: {
  edits: Record<string, string>
  onClear: (key: string) => void
  onEdit: (key: string, value: string) => void
  onSave: () => void
  onToggleEnabled?: (enabled: boolean) => void
  platform: MessagingPlatformInfo
  saving: string | null
  onOutlookOpenGuide?: () => void
  onOutlookTest?: () => void
  onOutlookTestConnection?: () => void
  outlookConnected?: boolean | null
}) {
  const { t } = useI18n()
  const m = t.messaging
  const [showAdvanced, setShowAdvanced] = useState(false)
  const outlookOnlyIds = platform.id === 'outlook'

  const hasEdits = Object.keys(trimEdits(edits)).length > 0
  const requiredFields = outlookOnlyIds
    ? platform.env_vars.filter(field => OUTLOOK_VISIBLE_KEYS.has(field.key))
    : platform.env_vars.filter(field => field.required)
  const optionalFields = outlookOnlyIds
    ? []
    : platform.env_vars.filter(field => !field.required && !fieldCopy(field, m).advanced)
  const advancedFields = outlookOnlyIds
    ? platform.env_vars.filter(field => field.key === 'OUTLOOK_INTERACTIVE_AUTH_FLOW')
    : platform.env_vars.filter(field => !field.required && fieldCopy(field, m).advanced)
  const hiddenCount = advancedFields.length
  const isSavingEnv = saving === `env:${platform.id}`
  const isTesting = saving === `test:${platform.id}`
  const isTestingConnection = saving === `connection:${platform.id}`
  const hasOutlookSavedCreds =
    platform.id === 'outlook' &&
    platform.env_vars.some(e => e.key === 'OUTLOOK_TENANT_ID' && e.is_set) &&
    platform.env_vars.some(e => e.key === 'OUTLOOK_CLIENT_ID' && e.is_set)
  const hasOutlookTypedCreds =
    platform.id === 'outlook' &&
    Boolean((edits.OUTLOOK_TENANT_ID || '').trim() && (edits.OUTLOOK_CLIENT_ID || '').trim())
  const badge = messagingBadge(platform, m, { edits, outlookConnected })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-5 px-5 py-4">
          <header className="flex items-start gap-3">
            <PlatformAvatar platformId={platform.id} platformName={platform.name} />
            <div className="min-w-0 flex-1">
              <h3 className="text-[0.9375rem] font-semibold tracking-tight">{platform.name}</h3>
              <p className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                {platform.description || introCopy(platform, m)}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatePill tone={badge.tone}>{badge.label}</StatePill>
              </div>
            </div>
          </header>

          {platform.error_message && (
            <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{platform.error_message}</span>
            </div>
          )}

          {platform.id === 'outlook' && onToggleEnabled && (
            <section className="flex items-start justify-between gap-4 rounded-xl border border-border px-3 py-2.5">
              <div className="min-w-0">
                <p className="text-[0.8125rem] font-medium">{m.outlookMessagingToggleLabel}</p>
                <p className="mt-0.5 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                  {m.outlookMessagingToggleHelp}
                </p>
              </div>
              <Switch
                aria-label={platform.enabled ? m.disableAria(platform.name) : m.enableAria(platform.name)}
                checked={platform.enabled}
                disabled={saving === `enabled:${platform.id}`}
                onCheckedChange={checked => onToggleEnabled(checked)}
              />
            </section>
          )}

          <section>
            <SectionTitle>{m.getCredentials}</SectionTitle>
            <p className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
              {introCopy(platform, m)}
            </p>
            <div className="mt-3">
              <p className="text-xs font-medium text-muted-foreground">
                {platform.id === 'outlook' ? m.openSetupGuide : m.contactSystemAdmin}
              </p>
              {platform.id === 'outlook' && onOutlookOpenGuide && (
                <Button className="mt-2" onClick={onOutlookOpenGuide} size="sm" variant="outline">
                  <ExternalLink className="size-3.5" />
                  {m.openSetupGuide}
                </Button>
              )}
            </div>
          </section>

          <section>
            <SectionTitle>{m.required}</SectionTitle>
            <div className="mt-3 grid gap-1">
              {requiredFields.length > 0 ? (
                requiredFields.map(field => (
                  <MessagingField
                    edits={edits}
                    field={field}
                    key={field.key}
                    onClear={onClear}
                    onEdit={onEdit}
                    saving={saving}
                  />
                ))
              ) : (
                <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                  {m.noTokenNeeded}
                </p>
              )}
            </div>
          </section>

          {optionalFields.length > 0 && (
            <section>
              <SectionTitle>{m.recommended}</SectionTitle>
              <div className="mt-3 grid gap-1">
                {optionalFields.map(field => (
                  <MessagingField
                    edits={edits}
                    field={field}
                    key={field.key}
                    onClear={onClear}
                    onEdit={onEdit}
                    saving={saving}
                  />
                ))}
              </div>
            </section>
          )}

          {hiddenCount > 0 && (
            <section>
              <button
                className="flex w-full items-center justify-between gap-2 py-0.5 text-left text-[0.7rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground"
                onClick={() => setShowAdvanced(value => !value)}
                type="button"
              >
                <span>{m.advanced(hiddenCount)}</span>
                <DisclosureCaret open={showAdvanced} size="0.875rem" />
              </button>
              {showAdvanced && (
                <div className="mt-3 grid gap-1">
                  {advancedFields.map(field => (
                    <MessagingField
                      edits={edits}
                      field={field}
                      key={field.key}
                      onClear={onClear}
                      onEdit={onEdit}
                      saving={saving}
                    />
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
      </div>

      <footer className="bg-(--ui-chat-surface-background) px-5 py-2.5">
        <div className="mx-auto flex max-w-2xl flex-wrap items-center gap-2">
          <div className="ml-auto flex w-full items-center justify-end gap-2">
            {hasEdits && <span className="text-xs text-muted-foreground">{m.unsavedChanges}</span>}
            {platform.id === 'outlook' && (
              <StatePill tone={badge.tone}>{badge.label}</StatePill>
            )}
            {platform.id === 'outlook' && onOutlookTest && (
              <Button
                onClick={onOutlookTest}
                size="sm"
                variant="outline"
                disabled={isTesting || (!hasOutlookTypedCreds && !hasOutlookSavedCreds)}
              >
                <ExternalLink className="size-3.5" />
                {isTesting ? 'Signing in...' : 'Start Auth'}
              </Button>
            )}
            {platform.id === 'outlook' && onOutlookTestConnection && (
              <Button
                onClick={onOutlookTestConnection}
                size="sm"
                variant="outline"
                disabled={isTestingConnection || (!hasOutlookTypedCreds && !hasOutlookSavedCreds)}
              >
                {isTestingConnection ? 'Testing...' : 'Test Connection'}
              </Button>
            )}
            <Button disabled={!hasEdits || isSavingEnv} onClick={onSave} size="sm">
              <Save />
              {isSavingEnv ? m.saving : m.saveChanges}
            </Button>
          </div>
        </div>
      </footer>
    </div>
  )
}

const PLATFORM_INTRO: Record<string, string> = {
  telegram:
    'In Telegram, talk to @BotFather, run /newbot, and copy the token it gives you. Then grab your numeric user ID from @userinfobot.',
  discord:
    'Open the Discord Developer Portal, create an application, add a Bot, then copy its token. Invite the bot to your server with the right scopes.',
  slack:
    'Create a Slack app, enable Socket Mode, install it to your workspace, then copy the bot token and app-level token.',
  mattermost:
    'On your Mattermost server, create a bot account or personal access token, then paste the server URL and token here.',
  matrix: 'Sign in to your homeserver with the bot account, then copy the access token, user ID, and homeserver URL.',
  signal:
    'Run a signal-cli REST bridge somewhere reachable, then point Hermes at the URL and the registered phone number.',
  whatsapp:
    'Start the WhatsApp bridge that ships with Hermes, scan the QR code on first run, then enable the platform.',
  bluebubbles:
    'Run BlueBubbles Server on a Mac with iMessage, expose its API, then point Hermes at the URL with the server password.',
  homeassistant:
    'In Home Assistant, open your profile and create a long-lived access token. Paste it here along with your HA URL.',
  email:
    'Use a dedicated mailbox. For Gmail/Workspace, create an app password and use imap.gmail.com / smtp.gmail.com.',
  outlook:
    'Create Azure app, grant delegated Mail.Read and Mail.Send, then set tenant ID + client ID. Use Test to complete device flow.',
  line: 'Get LINE credentials from your system administrator, then add them here to connect Hermes.',
  teams:
    'Ask your system administrator to register Microsoft Teams and share the bot credentials required for Hermes.',
  msteams:
    'Ask your system administrator to register Microsoft Teams and share the bot credentials required for Hermes.',
  sms: 'Get your Twilio Account SID and Auth Token from the Twilio console, plus a phone number that can send SMS.',
  dingtalk: 'Create a DingTalk app in the developer console, then copy the Client ID (App key) and Client Secret here.',
  feishu:
    'Create a Feishu / Lark app, configure the bot capability, and copy the App ID, App secret, and event encryption keys.',
  wecom:
    'Add a group robot in WeCom and copy its webhook key as WECOM_BOT_ID. Send-only — use the WeCom (app) option for two-way.',
  wecom_callback:
    'Set up a WeCom self-built app, expose its callback URL, and provide the corp ID, secret, agent ID, and AES key.',
  weixin:
    'Sign in to the WeChat Official Account platform, copy the AppID and Token, and point the message callback URL at Hermes.',
  qqbot: 'Register an app on the QQ Open Platform (q.qq.com) and copy the App ID and Client Secret.',
  api_server:
    'Expose Hermes as an OpenAI-compatible API. Set an auth key, then point Open WebUI / LobeChat / etc. at the host:port.',
  webhook:
    'Run an HTTP server that other tools (GitHub, GitLab, custom apps) can POST to. Use the secret to verify signatures.'
}

const introCopy = (platform: MessagingPlatformInfo, m: Translations['messaging']) =>
  m.platformIntro[platform.id] || PLATFORM_INTRO[platform.id] || platform.description

function MessagingField({
  edits,
  field,
  onClear,
  onEdit,
  saving
}: {
  edits: Record<string, string>
  field: MessagingEnvVarInfo
  onClear: (key: string) => void
  onEdit: (key: string, value: string) => void
  saving: string | null
}) {
  const { t } = useI18n()
  const m = t.messaging
  const copy = fieldCopy(field, m)
  const fieldId = `messaging-field-${field.key}`

  if (field.options) {
    const currentValue = edits[field.key] ?? field.value ?? field.options[0]?.value ?? ''
    return (
      <ListRow
        action={
          <Select onValueChange={next => onEdit(field.key, next)} value={currentValue}>
            <SelectTrigger className={CREDENTIAL_CONTROL_CLASS} id={fieldId}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {field.options.map(option => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={copy.help}
        title={<label htmlFor={fieldId}>{copy.label}</label>}
      />
    )
  }

  return (
    <ListRow
      action={
        <div className="flex items-center gap-2">
          <Input
            className={CREDENTIAL_CONTROL_CLASS}
            id={fieldId}
            onChange={event => onEdit(field.key, event.target.value)}
            placeholder={field.is_set ? field.redacted_value || m.replaceValue : copy.placeholder}
            type={field.is_password ? 'password' : 'text'}
            value={edits[field.key] || ''}
          />
          {field.url && (
            <Button asChild className="size-8 shrink-0" title={m.openDocs} variant="ghost">
              <a href={field.url} rel="noreferrer" target="_blank">
                <ExternalLink className="size-3.5" />
              </a>
            </Button>
          )}
          {field.is_set && (
            <Button
              className="size-8 shrink-0"
              disabled={saving === `clear:${field.key}`}
              onClick={() => onClear(field.key)}
              title={m.clearField(field.key)}
              variant="ghost"
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      }
      description={copy.help}
      title={
        <span className="flex flex-wrap items-center gap-2">
          <label htmlFor={fieldId}>{copy.label}</label>
          {field.is_set && <span className="text-[0.66rem] font-medium text-primary">{m.saved}</span>}
        </span>
      }
    />
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{children}</h4>
}

function StatePill({ children, tone }: { children: string; tone: StatusTone }) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.66rem] font-medium',
        PILL_TONE[tone]
      )}
    >
      <StatusDot tone={tone} />
      {children}
    </span>
  )
}
