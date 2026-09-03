import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  completeAimdsSuiteReauth,
  disconnectOAuthProvider,
  getAimdsSuiteStatus,
  getHermesConfigRecord,
  getMcpCatalog,
  getMicrosoftAdminConsentUrl,
  installMcpCatalogEntry,
  keycloakLogin,
  listOAuthProviders,
  removeMcpServer,
  saveHermesConfig,
  setEnvVar
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, Check, ChevronRight, ExternalLink, KeyRound, Loader2, ShieldCheck } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $desktopOnboarding, startManualProviderOAuth } from '@/store/onboarding'
import type { AimdsSuiteEnvStatus, EnvVarInfo, HermesConfigRecord, McpCatalogEntry, MicrosoftAdminConsentResponse, OAuthProvider } from '@/types/hermes'

import { ProviderKeyRows } from './credential-key-ui'
import { SettingsCategoryHeading, useEnvCredentials } from './env-credentials'
import { LoadingState, Pill, SettingsContent } from './primitives'

// Sub-views surfaced as a sidebar subnav: account sign-in and raw API keys.
export const PROVIDER_VIEWS = ['accounts', 'keys'] as const

export type ProviderView = (typeof PROVIDER_VIEWS)[number]

// Enterprise defaults baked in at packaging time (see vite.config.ts).
const DEFAULT_BASE_URL: string = import.meta.env.VITE_DEFAULT_BASE_URL ?? ''
const DEFAULT_REALM: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REALM ?? 'master'
const DEFAULT_REDIRECT_URI: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REDIRECT_URI ?? ''

function buildIamdsLiteLlmKeyGroup(vars: Record<string, EnvVarInfo>): ProviderKeyGroup[] {
  const mainKey = 'IAMDS_LITELLM_API_KEY'
  const mainInfo = vars[mainKey]

  if (!mainInfo) {
    return []
  }

  const groups: ProviderKeyGroup[] = [
    {
      advanced: [],
      description: 'AIMDS-Suite API key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: mainInfo.is_set,
      name: 'AIMDS-Suite',
      primary: [mainKey, mainInfo],
      priority: 0
    }
  ]

  const stagingInfo = vars.IAMDS_LITELLM_STAGING_API_KEY

  if (stagingInfo) {
    groups.push({
      advanced: [],
      description: 'AIMDS-Suite (Staging) API key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: stagingInfo.is_set,
      name: 'AIMDS-Suite (Staging)',
      primary: ['IAMDS_LITELLM_STAGING_API_KEY', stagingInfo],
      priority: 1
    })
  }

  const devInfo = vars.IAMDS_LITELLM_DEV_API_KEY

  if (devInfo) {
    groups.push({
      advanced: [],
      description: 'AIMDS-Suite (Development) API key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: devInfo.is_set,
      name: 'AIMDS-Suite (Development)',
      primary: ['IAMDS_LITELLM_DEV_API_KEY', devInfo],
      priority: 2
    })
  }

  const localDevInfo = vars.IAMDS_LITELLM_LOCALDEV_API_KEY

  if (localDevInfo) {
    groups.push({
      advanced: [],
      description: 'AIMDS-Suite (Local Dev) API key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: localDevInfo.is_set,
      name: 'AIMDS-Suite (Local Dev)',
      primary: ['IAMDS_LITELLM_LOCALDEV_API_KEY', localDevInfo],
      priority: 3
    })
  }

  return groups
}

function normalizeProviderBaseUrl(raw: string): string {
  const trimmed = raw.trim()

  if (!trimmed) {return ''}
  const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
  let parsed: URL

  try {
    parsed = new URL(candidate)
  } catch {
    return ''
  }

  if (!parsed.hostname || (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')) {
    return ''
  }

  parsed.hash = ''
  const cleaned = `${parsed.origin}${parsed.pathname}${parsed.search}`.replace(/\/+$/, '')

  if (cleaned.endsWith('/litellm/v1')) {return cleaned}

  if (cleaned.endsWith('/litellm/mcp')) {return `${cleaned.slice(0, -'/litellm/mcp'.length)}/litellm/v1`}

  return `${cleaned}/litellm/v1`
}

function toEditableBaseUrl(configuredUrl: string): string {
  const trimmed = configuredUrl.trim().replace(/\/+$/, '')

  if (trimmed.endsWith('/litellm/v1')) {return trimmed.slice(0, -'/litellm/v1'.length)}

  if (trimmed.endsWith('/litellm/mcp')) {return trimmed.slice(0, -'/litellm/mcp'.length)}

  return trimmed
}

function readProviderBaseUrl(config: Record<string, unknown>, slug: string): string {
  const providers = config.providers

  if (!providers || typeof providers !== 'object' || Array.isArray(providers)) {return ''}
  const entry = (providers as Record<string, unknown>)[slug]

  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {return ''}
  const baseUrl = (entry as Record<string, unknown>).base_url

  return typeof baseUrl === 'string' ? toEditableBaseUrl(baseUrl) : ''
}

// One row per AIMDS-Suite environment: URL field + backend-derived status +
// an always-available Keycloak SSO / re-auth button (AIS-286). No default
// host is ever shown as configured; the backend decides connected /
// needs re-auth / not configured / unreachable.
const SUITE_ENVS = [
  { id: 'aimds-suite-prod', label: 'Production', keyEnv: 'IAMDS_LITELLM_API_KEY', legacySlug: 'iamds-litellm', placeholder: 'https://suite.iamds.com' },
  { id: 'aimds-suite-staging', label: 'Staging', keyEnv: 'IAMDS_LITELLM_STAGING_API_KEY', legacySlug: 'iamds-litellm-staging', placeholder: 'https://staging.suite.iamds.com' },
  { id: 'aimds-suite-dev', label: 'Development', keyEnv: 'IAMDS_LITELLM_DEV_API_KEY', legacySlug: 'iamds-litellm-dev', placeholder: 'https://dev.suite.iamds.com' },
  { id: 'aimds-suite-localdev', label: 'Local Dev', keyEnv: 'IAMDS_LITELLM_LOCALDEV_API_KEY', legacySlug: 'iamds-litellm-localdev', placeholder: 'http://localhost:8000' }
] as const

type SuiteEnvId = (typeof SUITE_ENVS)[number]['id']

const SUITE_ENV_NAMES: Record<SuiteEnvId, string> = {
  'aimds-suite-prod': 'AIMDS-Suite',
  'aimds-suite-staging': 'AIMDS-Suite (Staging)',
  'aimds-suite-dev': 'AIMDS-Suite (Development)',
  'aimds-suite-localdev': 'AIMDS-Suite (Local Dev)'
}

function suiteReasonText(t: ReturnType<typeof useI18n>['t'], status: AimdsSuiteEnvStatus | undefined): string {
  if (!status) {
    return ''
  }

  const r = t.settings.providers.suite.reason

  if (status.reason === 'key_missing') {return r.keyMissing}

  if (status.reason === 'url_missing') {return r.urlMissing}

  if (status.reason === 'env_mismatch') {return r.envMismatch}

  if (status.reason.startsWith('http_40')) {return r.unauthorized}

  if (status.reason.startsWith('runtime_')) {return r.runtime}

  if (status.reason === 'network' || status.reason.startsWith('http_5')) {return r.network}

  if (status.reason === 'ok') {return r.ok}

  return ''
}

function SuiteEnvStatusPill({ status }: { status: AimdsSuiteEnvStatus | undefined }) {
  const { t } = useI18n()
  const labels = t.settings.providers.suite

  if (!status) {
    return <span className="text-xs text-muted-foreground">{labels.checking}</span>
  }

  const tone =
    status.state === 'connected'
      ? 'text-emerald-500'
      : status.state === 'needs_reauth'
        ? 'text-amber-500'
        : status.state === 'unreachable'
          ? 'text-muted-foreground'
          : 'text-muted-foreground'

  const text =
    status.state === 'connected'
      ? labels.connected
      : status.state === 'needs_reauth'
        ? labels.needsReauth
        : status.state === 'unreachable'
          ? labels.unreachable
          : labels.notConfigured

  const host = status.base_url ? status.base_url.replace(/^https?:\/\//, '').replace(/\/litellm(\/v1)?$/, '') : ''

  return (
    <span className={cn('inline-flex flex-col items-end text-xs font-medium', tone)} title={suiteReasonText(t, status)}>
      <span className="inline-flex items-center gap-1.5">
        {status.state === 'connected' ? <Check className="size-3.5" /> : status.state === 'needs_reauth' ? <AlertCircle className="size-3.5" /> : null}
        {text}
      </span>
      {host && <span className="font-normal text-muted-foreground">{host}</span>}
    </span>
  )
}

function IamdsExtraProvidersPanel({ onRefreshCreds }: { onRefreshCreds?: () => void } = {}) {
  const { t } = useI18n()
  const labels = t.settings.providers.suite

  const [urls, setUrls] = useState<Record<SuiteEnvId, string>>({
    'aimds-suite-prod': '',
    'aimds-suite-staging': '',
    'aimds-suite-dev': '',
    'aimds-suite-localdev': ''
  })

  const [isSaving, setIsSaving] = useState(false)
  const [statuses, setStatuses] = useState<Partial<Record<SuiteEnvId, AimdsSuiteEnvStatus>>>({})
  const [busyEnv, setBusyEnv] = useState<null | SuiteEnvId>(null)

  const loadStatuses = useCallback(async (probe = true) => {
    try {
      const res = await getAimdsSuiteStatus({ probe })
      const next: Partial<Record<SuiteEnvId, AimdsSuiteEnvStatus>> = {}

      for (const env of res.environments) {
        next[env.id as SuiteEnvId] = env
      }

      setStatuses(next)
    } catch {
      // Status is advisory; keep the last known values.
    }
  }, [])

  const loadUrls = useCallback(async () => {
    try {
      const config = await getHermesConfigRecord()
      const next = { ...urls }

      for (const env of SUITE_ENVS) {
        // Only what is configured — never a baked-in default host (AIS-286).
        next[env.id] = readProviderBaseUrl(config, env.id) || readProviderBaseUrl(config, env.legacySlug)
      }

      setUrls(next)
    } catch {
      // Best-effort prefill only.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void loadUrls()
    void loadStatuses(true)
  }, [loadUrls, loadStatuses])

  const handleKeycloakLogin = async (env: (typeof SUITE_ENVS)[number]) => {
    const baseUrl = urls[env.id].trim()

    if (!baseUrl) {
      notify({ kind: 'error', message: labels.enterUrlFirst, title: SUITE_ENV_NAMES[env.id] })

      return
    }

    setBusyEnv(env.id)

    try {
      let rootDomain = baseUrl.replace(/\/+$/, '')

      for (const suffix of ['/litellm/v1', '/litellm', '/auth']) {
        if (rootDomain.endsWith(suffix)) {
          rootDomain = rootDomain.slice(0, -suffix.length).replace(/\/+$/, '')
        }
      }

      const redirectUri = DEFAULT_REDIRECT_URI || 'hermes://callback'
      const result = await keycloakLogin({ baseUrl: rootDomain, realm: DEFAULT_REALM, redirectUri })

      // Persist the URL for this environment first so the key and the host
      // are stored as a pair, then the key, then make both effective.
      await persistUrls({ ...urls, [env.id]: baseUrl }, { silent: true })
      await setEnvVar(env.keyEnv, result.apiKey)

      try {
        await completeAimdsSuiteReauth(env.id)
      } catch {
        // The next 401 self-heals via the credential refresh path.
      }

      if (env.id === 'aimds-suite-prod') {
        try {
          const config = await getHermesConfigRecord()

          if (!config.model) {
            await saveHermesConfig({ ...config, model: 'aimds-suite-prod/AIMDS-Suite-Auto' })
          }
        } catch {
          // Best-effort model selection
        }
      }

      onRefreshCreds?.()
      await loadStatuses(true)
      notify({ kind: 'success', message: `${SUITE_ENV_NAMES[env.id]}: ${labels.connected}`, title: labels.connected })
    } catch (err) {
      notifyError(err, 'Keycloak SSO failed')
    } finally {
      setBusyEnv(null)
    }
  }

  const persistUrls = async (values: Record<SuiteEnvId, string>, options: { silent?: boolean } = {}) => {
    const normalized = {} as Record<SuiteEnvId, string>

    for (const env of SUITE_ENVS) {
      const raw = values[env.id].trim()
      const value = raw ? normalizeProviderBaseUrl(raw) : ''

      if (raw && !value) {
        notify({ kind: 'error', message: `${env.label} URL is invalid`, title: 'Could not save provider URLs' })

        return false
      }

      normalized[env.id] = value
    }

    const config = await getHermesConfigRecord()

    const providers = config.providers && typeof config.providers === 'object' && !Array.isArray(config.providers)
      ? { ...(config.providers as Record<string, unknown>) }
      : {}

    for (const env of SUITE_ENVS) {
      const baseUrl = normalized[env.id]

      if (!baseUrl) {
        delete providers[env.id]
        delete providers[env.legacySlug]

        continue
      }

      const existingRaw = providers[env.id]

      const existing =
        existingRaw && typeof existingRaw === 'object' && !Array.isArray(existingRaw)
          ? { ...(existingRaw as Record<string, unknown>) }
          : {}

      existing.name = SUITE_ENV_NAMES[env.id]
      existing.base_url = baseUrl
      existing.key_env = env.keyEnv
      existing.transport = 'codex_responses'
      providers[env.id] = existing
      delete providers[env.legacySlug]
    }

    // The backend mirrors providers.* into the *_BASE_URL env vars itself.
    await saveHermesConfig({ ...config, providers })

    if (!options.silent) {
      notify({ kind: 'success', message: labels.saved, title: 'Saved' })
    }

    return true
  }

  const saveUrls = async () => {
    setIsSaving(true)

    try {
      if (await persistUrls(urls)) {
        await loadStatuses(true)
      }
    } catch (err) {
      notifyError(err, 'Failed to save provider URLs')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="mt-4 rounded-[8px] border border-border bg-muted/20 p-3">
      <h3 className="text-sm font-medium text-foreground">{labels.title}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{labels.intro}</p>

      <div className="mt-3 grid gap-3">
        {SUITE_ENVS.map(env => {
          const status = statuses[env.id]
          const hasUrl = Boolean(urls[env.id].trim())
          const buttonLabel = status?.key_present ? labels.reauthenticate : labels.signInSso

          return (
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2" key={env.id}>
              <label className="text-xs font-medium text-foreground min-w-44 shrink-0" htmlFor={`iamds-${env.id}-url`}>
                {env.label}
              </label>
              <div className="flex items-center gap-2 flex-1 max-w-xl sm:ml-auto">
                <Input
                  className="text-xs flex-1"
                  id={`iamds-${env.id}-url`}
                  onChange={e => setUrls(prev => ({ ...prev, [env.id]: e.target.value }))}
                  placeholder={env.placeholder}
                  value={urls[env.id]}
                />
                <div className="shrink-0 min-w-44 flex items-center justify-end gap-2">
                  <SuiteEnvStatusPill status={status} />
                  <Button
                    disabled={!hasUrl || busyEnv !== null}
                    onClick={() => void handleKeycloakLogin(env)}
                    size="sm"
                    title={hasUrl ? undefined : labels.enterUrlFirst}
                    variant={status?.state === 'needs_reauth' ? 'default' : 'outline'}
                  >
                    {busyEnv === env.id ? <Loader2 className="size-3.5 animate-spin" /> : buttonLabel}
                  </Button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-3 flex justify-end">
        <Button disabled={isSaving} onClick={() => void saveUrls()} size="sm">
          {isSaving ? labels.saving : labels.saveUrls}
        </Button>
      </div>
    </section>
  )
}

function IamdsAccountPanel({ onWantApiKey, onRefreshCreds }: { onWantApiKey: () => void; onRefreshCreds?: () => void }) {
  const { t } = useI18n()
  const [isKeycloakLoading, setIsKeycloakLoading] = useState(false)
  const [keycloakConnected, setKeycloakConnected] = useState(false)
  const [keycloakError, setKeycloakError] = useState<string | null>(null)

  const handleKeycloakLogin = async (overrideBaseUrl?: string, targetKeyEnv?: string) => {
    setKeycloakError(null)
    setIsKeycloakLoading(true)
    setKeycloakConnected(false)

    const targetKey = targetKeyEnv || 'IAMDS_LITELLM_API_KEY'
    let baseUrl = (overrideBaseUrl || DEFAULT_BASE_URL).trim()

    if (!baseUrl) {
      // No baked-in default: use the URL configured for production (AIS-286).
      try {
        const res = await getAimdsSuiteStatus()
        baseUrl = res.environments.find(e => e.id === 'aimds-suite-prod')?.base_url ?? ''
      } catch {
        baseUrl = ''
      }
    }

    if (!baseUrl) {
      setKeycloakError(t.settings.providers.suite.enterUrlFirst)
      setIsKeycloakLoading(false)

      return
    }

    try {
      let rootDomain = baseUrl.trim().replace(/\/+$/, '')

      for (const suffix of ['/litellm/v1', '/litellm', '/auth']) {
        if (rootDomain.endsWith(suffix)) {
          rootDomain = rootDomain.slice(0, -suffix.length).replace(/\/+$/, '')
        }
      }

      const redirectUri = DEFAULT_REDIRECT_URI || 'hermes://callback'

      const result = await keycloakLogin({
        baseUrl: rootDomain,
        realm: DEFAULT_REALM,
        redirectUri,
      })

      await setEnvVar(targetKey, result.apiKey)

      try {
        await completeAimdsSuiteReauth('aimds-suite-prod')
      } catch {
        // The next 401 self-heals via the credential refresh path.
      }

      try {
        const config = await getHermesConfigRecord()

        if (!config.model) {
          await saveHermesConfig({ ...config, model: 'aimds-suite-prod/AIMDS-Suite-Auto' })
        }
      } catch {
        // Best-effort model selection
      }

      setKeycloakConnected(true)
      const label = targetKey === 'IAMDS_LITELLM_STAGING_API_KEY' ? 'AIMDS-Suite Staging' : targetKey === 'IAMDS_LITELLM_DEV_API_KEY' ? 'AIMDS-Suite Development' : 'AIMDS-Suite'
      notify({ kind: 'success', message: `API key obtained via Keycloak SSO (${label})`, title: 'Connected' })
      onRefreshCreds?.()
    } catch (err) {
      setKeycloakError(err instanceof Error ? err.message : String(err))
    } finally {
      setIsKeycloakLoading(false)
    }
  }

  return (
    <section className="mb-5 grid gap-2">
      <SettingsCategoryHeading icon={KeyRound} title={t.settings.providers.connectAccount} />

      {/* Keycloak SSO — primary when DEFAULT_BASE_URL is baked in */}
      {DEFAULT_BASE_URL && (
        <div className="rounded-[8px] border border-border bg-muted/20 p-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[length:var(--conversation-text-font-size)] font-semibold">AIMDS-Suite</span>
            <span className="inline-flex items-center gap-1.5 bg-primary px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-primary-foreground">
              <span aria-hidden="true" className="dither inline-block size-2 shrink-0" />
              {t.onboarding.recommended}
            </span>
          </div>
          <p className="mb-3 text-xs leading-5 text-muted-foreground">
            Sign in with your organisation account to automatically obtain an API key.
          </p>
          <button
            className="flex w-full items-center justify-center gap-2 rounded border border-primary bg-primary/10 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
            disabled={isKeycloakLoading}
            onClick={() => void handleKeycloakLogin()}
            type="button"
          >
            {isKeycloakLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Connecting…
              </>
            ) : keycloakConnected ? (
              <>
                <Check className="h-4 w-4 text-green-500" />
                Connected via Keycloak
              </>
            ) : (
              <>
                <ShieldCheck className="h-4 w-4" />
                Connect with Keycloak
              </>
            )}
          </button>
          {keycloakError && (
            <p className="mt-2 flex items-center gap-1 text-xs text-red-500">
              <AlertCircle className="h-3 w-3 shrink-0" />
              {keycloakError}
            </p>
          )}
        </div>
      )}

      {/* Manual API key — always available as secondary path */}
      <button
        className="group relative flex w-full items-center justify-between gap-4 rounded-[8px] bg-primary/[0.06] px-3 py-2.5 text-left transition-colors hover:bg-primary/10"
        onClick={onWantApiKey}
        type="button"
      >
        <span aria-hidden className="arc-border arc-reverse arc-nous" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[length:var(--conversation-text-font-size)] font-semibold">
              AIMDS-Suite
            </span>
            {!DEFAULT_BASE_URL && (
              <span className="inline-flex items-center gap-1.5 bg-primary px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-primary-foreground">
                <span aria-hidden="true" className="dither inline-block size-2 shrink-0" />
                {t.onboarding.recommended}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {DEFAULT_BASE_URL
              ? 'Configure your API key manually instead of using SSO'
              : 'Configure your API key to access IAMDS-hosted models'}
          </p>
        </div>
        <ChevronRight className="size-4 shrink-0 text-primary transition group-hover:translate-x-0.5" />
      </button>
    </section>
  )
}

// Tenant onboarding for the Microsoft 365 app (AIS-286): a tenant admin
// approves the org-consent tier once; afterwards every signed-in user gets
// Teams chat / presence / shared mailboxes / To Do silently.
function M365TenantConsentControl({ loggedIn }: { loggedIn: boolean }) {
  const { t } = useI18n()
  const m = t.settings.providers.m365
  const [info, setInfo] = useState<MicrosoftAdminConsentResponse | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!loggedIn) {
      setInfo(null)

      return
    }

    getMicrosoftAdminConsentUrl().then(setInfo).catch(() => setInfo(null))
  }, [loggedIn])

  const openConsent = async () => {
    setBusy(true)

    try {
      const res = info ?? (await getMicrosoftAdminConsentUrl())
      setInfo(res)
      window.open(res.url, '_blank', 'noopener,noreferrer')
      await navigator.clipboard.writeText(res.url).catch(() => undefined)
      notify({ kind: 'info', message: m.openedAndCopied, title: m.grantForOrg })
    } catch (err) {
      notifyError(err, m.loadFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {info && loggedIn && (
        <Pill tone={info.org_consented ? 'primary' : 'muted'}>{info.org_consented ? m.orgApproved : m.selfOnly}</Pill>
      )}
      {!info?.org_consented && (
        <Button disabled={busy} onClick={() => void openConsent()} size="xs" title={m.grantForOrgHint} variant="outline">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : m.grantForOrg}
        </Button>
      )}
    </div>
  )
}

export function OAuthAccountsPanel() {
  const [providers, setProviders] = useState<OAuthProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [disconnecting, setDisconnecting] = useState<null | string>(null)

  const loadProviders = useCallback(async () => {
    try {
      const res = await listOAuthProviders()
      const all = res.providers || []
      // Respect the backend's own `hidden` flag (e.g. iamds-keycloak, which
      // is surfaced elsewhere) instead of re-implementing an ad-hoc
      // allowlist here — do NOT filter to GitHub only.
      setProviders(all.filter(p => !p.hidden))
    } catch (err) {
      console.error('Failed to load OAuth providers', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadProviders()

    const handleFocus = () => {
      void loadProviders()
    }

    window.addEventListener('focus', handleFocus)

    const unsubscribe = $desktopOnboarding.subscribe(() => {
      void loadProviders()
    })

    return () => {
      window.removeEventListener('focus', handleFocus)
      unsubscribe()
    }
  }, [loadProviders])

  const handleDisconnect = async (id: string) => {
    setDisconnecting(id)

    try {
      await disconnectOAuthProvider(id)
      await loadProviders()
      notify({ kind: 'success', message: 'Disconnected account', title: 'Account disconnected' })
    } catch (err) {
      notifyError(err, 'Failed to disconnect account')
    } finally {
      setDisconnecting(null)
    }
  }

  if (loading || providers.length === 0) {
    return null
  }

  return (
    <section className="mb-5 grid gap-2">
      <SettingsCategoryHeading icon={KeyRound} title="Connected Accounts (OAuth)" />
      <div className="grid gap-2">
        {providers.map(p => {
          const loggedIn = p.status?.logged_in

          return (
            <div
              className="flex items-center justify-between gap-4 rounded-[8px] border border-border bg-muted/20 p-3"
              key={p.id}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">{p.name}</span>
                  {loggedIn ? (
                    <Pill tone="primary">Connected</Pill>
                  ) : (
                    <Pill tone="muted">Not connected</Pill>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {loggedIn
                    ? p.status?.source_label || p.status?.token_preview || 'Authenticated'
                    : `Authenticate using ${p.flow === 'device_code' ? 'device verification code' : 'OAuth'}`}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {p.id === 'microsoft' && <M365TenantConsentControl loggedIn={Boolean(loggedIn)} />}
                {loggedIn ? (
                  <Button
                    disabled={disconnecting === p.id}
                    onClick={() => void handleDisconnect(p.id)}
                    size="xs"
                    variant="outline"
                  >
                    {disconnecting === p.id ? 'Disconnecting…' : 'Disconnect'}
                  </Button>
                ) : (
                  <Button
                    onClick={() => startManualProviderOAuth(p.id)}
                    size="xs"
                    variant="default"
                  >
                    Connect
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// Sentinel for the multi-instance install dialog's instance picker,
// meaning "create a new named instance" rather than editing one of the
// entry's already-installed instances (see McpCatalogEntry.instances).
const NEW_INSTANCE_VALUE = '__new__'

function McpCatalogSection({
  vars,
  onRefreshCreds

}: {
  vars: Record<string, EnvVarInfo>
  onRefreshCreds?: () => void
}) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const [catalogEntries, setCatalogEntries] = useState<McpCatalogEntry[]>([])
  const [installedServers, setInstalledServers] = useState<Record<string, unknown>>({})
  const [installModalEntry, setInstallModalEntry] = useState<McpCatalogEntry | null>(null)
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})
  const [installing, setInstalling] = useState(false)
  const [loading, setLoading] = useState(true)
  // Multi-instance install/edit picker (AtlassianMCP/TempoMCP only, per
  // McpCatalogEntry.multi_instance). '__new__' means "create a new named
  // instance"; any other value is the name of an already-installed
  // instance being edited in place.
  const [instancePickerValue, setInstancePickerValue] = useState<string>(NEW_INSTANCE_VALUE)
  const [newInstanceName, setNewInstanceName] = useState('')

  const loadCatalogAndConfig = async () => {
    setLoading(true)

    try {
      const [catRes, cfg] = await Promise.all([
        getMcpCatalog().catch(() => ({ entries: [] })),
        getHermesConfigRecord().catch(() => ({} as HermesConfigRecord))
      ])

      if (catRes.entries) {
        setCatalogEntries(catRes.entries)
      }

      const rawServers = cfg?.mcp_servers

      if (rawServers && typeof rawServers === 'object' && !Array.isArray(rawServers)) {
        setInstalledServers(rawServers as Record<string, unknown>)
      } else {
        setInstalledServers({})
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadCatalogAndConfig()
  }, [])

  const envValuesForInstance = (entry: McpCatalogEntry, instanceName: string): Record<string, string> => {
    const envVars = entry.required_env ?? entry.auth?.env ?? []
    const serverCfg = installedServers[instanceName] as { env?: Record<string, string> } | undefined
    const values: Record<string, string> = {}

    for (const item of envVars) {
      const raw = serverCfg?.env?.[item.name]

      // A secondary instance's credentials are stored as literal strings
      // (see hermes_cli/mcp_catalog.py's literal_env); a ${VAR} template
      // means "resolved from the shared .env", so fall back to the same
      // current_value/default flow used for a fresh/default install.
      if (raw && !(raw.startsWith('${') && raw.endsWith('}'))) {
        values[item.name] = raw
      } else {
        values[item.name] = item.current_value || item.default || ''
      }
    }

    return values
  }

  const openInstallModal = (entry: McpCatalogEntry) => {
    if (entry.disabled) {return}
    const hasExistingInstances = (entry.instances?.length ?? 0) > 0
    setInstancePickerValue(NEW_INSTANCE_VALUE)
    // First-ever install of a multi-instance entry still defaults to the
    // plain catalog name (e.g. "AtlassianMCP"); once at least one instance
    // exists, "create new" starts blank so the user must name the new one.
    setNewInstanceName(entry.multi_instance && !hasExistingInstances ? entry.name : '')
    setSecretInputs(entry.multi_instance ? {} : envValuesForInstance(entry, entry.name))
    setInstallModalEntry(entry)
  }

  const handleInstancePickerChange = (value: string) => {
    if (!installModalEntry) {return}
    setInstancePickerValue(value)

    if (value === NEW_INSTANCE_VALUE) {
      setNewInstanceName('')
      setSecretInputs({})
    } else {
      setSecretInputs(envValuesForInstance(installModalEntry, value))
    }
  }

  const resolvedInstanceName = installModalEntry?.multi_instance
    ? instancePickerValue === NEW_INSTANCE_VALUE
      ? newInstanceName.trim() || installModalEntry.name
      : instancePickerValue
    : installModalEntry?.name

  const handleInstallCatalog = async () => {
    if (!installModalEntry) {return}

    setInstalling(true)

    try {
      const result = await installMcpCatalogEntry({
        enable: true,
        name: installModalEntry.name,
        env: secretInputs,
        secrets: secretInputs,
        ...(installModalEntry.multi_instance ? { instance_name: resolvedInstanceName } : {})
      })

      if (result.ok) {
        notify({
          kind: 'success',
          message: m.catalogInstallSuccessMessage(result.name ?? installModalEntry.name),
          title: m.catalogInstallSuccessTitle
        })
        await loadCatalogAndConfig()
        onRefreshCreds?.()

        const provider = installModalEntry.auth?.provider
        const tokenEntered = Object.values(secretInputs).some(val => val && val.trim().length > 0)
        setInstallModalEntry(null)

        if (provider && !tokenEntered) {
          startManualProviderOAuth(provider)
        }
      } else {
        notify({ kind: 'error', message: result.message ?? m.saveFailed, title: m.saveFailed })
      }
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setInstalling(false)
    }
  }

  const handleUninstall = async (serverName: string) => {
    try {
      await removeMcpServer(serverName)
      notify({
        kind: 'success',
        message: `MCP Server '${serverName}' wurde entfernt`,
        title: 'Entfernt'
      })
      await loadCatalogAndConfig()
      onRefreshCreds?.()
      setInstallModalEntry(null)
    } catch (err) {
      notifyError(err, 'Fehler beim Entfernen des MCP Servers')
    }
  }

  if (loading) {
    return <LoadingState label={t.settings.providers.loading} />
  }

  if (catalogEntries.length === 0) {
    return (
      <section className="mb-6">
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-foreground">{m.catalogSectionTitle}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{m.catalogSectionDesc}</p>
        </div>
        <div className="rounded-lg border border-border bg-card p-8 text-center text-xs text-muted-foreground">
          Keine Katalog-Einträge verfügbar.
        </div>
      </section>
    )
  }

  return (
    <section className="mb-6">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-foreground">{m.catalogSectionTitle}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{m.catalogSectionDesc}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {catalogEntries.map(entry => {
          const isInstalled = Boolean(installedServers[entry.name])
          const isDisabled = entry.disabled === true
          const sourceStr = entry.source?.trim() || ''
          const isSourceUrl = sourceStr.startsWith('http://') || sourceStr.startsWith('https://')

          return (
            <div
              className={cn(
                "flex flex-col justify-between rounded-lg border border-border bg-card p-3.5 shadow-xs transition-colors",
                isDisabled ? "opacity-60" : "hover:border-primary/40"
              )}
              key={entry.name}
            >
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-sm capitalize">{entry.name}</span>
                  {isDisabled ? (
                    <Pill>In Entwicklung</Pill>
                  ) : isSourceUrl ? (
                    <a
                      className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted/80 hover:text-foreground transition-colors shrink-0"
                      href={sourceStr}
                      onClick={e => e.stopPropagation()}
                      rel="noopener noreferrer"
                      target="_blank"
                      title={sourceStr}
                    >
                      Source
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                  ) : (
                    <Pill>{sourceStr ? sourceStr.split(' ')[0] : 'catalog'}</Pill>
                  )}
                </div>
                <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">{entry.description}</p>
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-border/50 pt-2.5">
                <span className="text-xs text-muted-foreground">{isInstalled ? m.catalogInstalled : ''}</span>
                <div className="flex items-center gap-1.5">
                  {isInstalled && (
                    <Button
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => void handleUninstall(entry.name)}
                      size="xs"
                      variant="ghost"
                    >
                      Entfernen
                    </Button>
                  )}
                  <Button
                    disabled={isDisabled}
                    onClick={() => openInstallModal(entry)}
                    size="xs"
                    variant={isDisabled ? 'ghost' : isInstalled ? 'secondary' : 'default'}
                  >
                    {isDisabled ? 'In Entwicklung' : isInstalled ? m.editServer : m.catalogInstall}
                  </Button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <Dialog onOpenChange={open => !open && setInstallModalEntry(null)} open={Boolean(installModalEntry)}>
        {installModalEntry && (
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>{m.catalogModalTitle(installModalEntry.name)}</DialogTitle>
              <DialogDescription>{m.catalogModalDesc}</DialogDescription>
            </DialogHeader>

            {installModalEntry.auth?.notes && (
              <p className="rounded-md bg-muted/50 px-2.5 py-2 text-xs text-muted-foreground">
                {installModalEntry.auth.notes}
              </p>
            )}

            {installModalEntry.multi_instance && (
              <div className="grid gap-1.5 pt-1">
                <span className="font-medium text-xs">{m.catalogInstancePickerLabel}</span>
                <Select onValueChange={handleInstancePickerChange} value={instancePickerValue}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NEW_INSTANCE_VALUE}>{m.catalogInstanceCreateNew}</SelectItem>
                    {(installModalEntry.instances ?? []).map(name => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {instancePickerValue === NEW_INSTANCE_VALUE && (
                  <Input
                    onChange={e => setNewInstanceName(e.target.value)}
                    placeholder={installModalEntry.name}
                    value={newInstanceName}
                  />
                )}
              </div>
            )}

            <div className="grid gap-3 py-2">
              {(installModalEntry.required_env ?? installModalEntry.auth?.env ?? []).map(item => {
                // The "already in .env" hint only applies to the shared
                // ~/.hermes/.env, which secondary named instances never
                // touch (their credentials live as literal strings in
                // their own mcp_servers.<name>.env block) -- showing it
                // here would incorrectly imply a secondary instance's
                // field is already filled in when it isn't.
                const isSecondaryInstance =
                  installModalEntry.multi_instance && instancePickerValue !== NEW_INSTANCE_VALUE

                const isSet = !isSecondaryInstance && vars[item.name]?.is_set

                return (
                  <label className="grid gap-1" key={item.name}>
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-xs">
                        {item.prompt || item.name}
                        {item.required && <span className="ml-0.5 text-destructive">*</span>}
                      </span>
                      {isSet && (
                        <span className="text-[10px] text-green-500 font-medium">✓ Ist bereits in .env gesetzt</span>
                      )}
                    </div>
                    <Input
                      onChange={e =>
                        setSecretInputs(prev => ({
                          ...prev,
                          [item.name]: e.target.value
                        }))
                      }
                      placeholder={item.default || (isSet ? 'Bereits gesetzt (Eingabe überschreibt)' : item.name)}
                      type={item.secret ? 'password' : 'text'}
                      value={secretInputs[item.name] ?? ''}
                    />
                  </label>
                )
              })}
              <p className="text-[11px] text-muted-foreground">{m.catalogSecretsNotice}</p>
            </div>

            <DialogFooter className="flex items-center justify-between sm:justify-between">
              <div>
                {Boolean(resolvedInstanceName && installedServers[resolvedInstanceName]) && (
                  <Button
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => void handleUninstall(resolvedInstanceName as string)}
                    size="xs"
                    variant="ghost"
                  >
                    Entfernen
                  </Button>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button onClick={() => setInstallModalEntry(null)} size="xs" variant="ghost">
                  {t.common.cancel}
                </Button>
                <Button disabled={installing} onClick={() => void handleInstallCatalog()} size="xs">
                  {installing ? m.catalogInstalling : m.catalogInstall}
                </Button>
              </div>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </section>
  )
}

export function ProvidersSettings({ onViewChange, view }: ProvidersSettingsProps) {
  const { t } = useI18n()
  const { rowProps, vars, refetch } = useEnvCredentials()
  const [openProvider, setOpenProvider] = useState<null | string>(null)

  if (!vars) {
    return <LoadingState label={t.settings.providers.loading} />
  }

  const keyGroups = buildIamdsLiteLlmKeyGroup(vars)

  if (view === 'keys') {
    return (
      <SettingsContent>
        {keyGroups.length > 0 ? (
          <div className="grid gap-2">
            {keyGroups.map(group => (
              <ProviderKeyRows
                expanded={openProvider === group.name}
                group={group}
                key={group.name}
                onExpand={() => setOpenProvider(group.name)}
                onToggle={() => setOpenProvider(prev => (prev === group.name ? null : group.name))}
                rowProps={rowProps}
              />
            ))}
            <IamdsExtraProvidersPanel onRefreshCreds={() => void refetch()} />
          </div>
        ) : (
          <div className="grid min-h-32 place-items-center px-4 py-8 text-center text-[length:var(--conversation-caption-font-size)] text-muted-foreground">
            {t.settings.providers.noProviderKeys}
          </div>
        )}
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <IamdsAccountPanel onRefreshCreds={() => void refetch()} onWantApiKey={() => onViewChange('keys')} />
      <OAuthAccountsPanel />
    </SettingsContent>
  )
}

export function McpCatalogSettings({ onRefreshCreds }: { onRefreshCreds?: () => void } = {}) {
  const { t } = useI18n()
  const { vars, refetch } = useEnvCredentials()

  if (!vars) {
    return <LoadingState label={t.settings.providers.loading} />
  }

  return (
    <SettingsContent>
      <McpCatalogSection onRefreshCreds={() => { void refetch(); onRefreshCreds?.() }} vars={vars} />
    </SettingsContent>
  )
}

interface ProviderKeyGroup {
  advanced: [string, EnvVarInfo][]
  description?: string
  docsUrl?: string
  hasAnySet: boolean
  name: string
  primary: [string, EnvVarInfo]
  priority: number
}

interface ProvidersSettingsProps {
  onViewChange: (view: ProviderView) => void
  view: ProviderView
}
