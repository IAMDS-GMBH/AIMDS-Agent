import { useEffect, useState } from 'react'

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
import {
  getHermesConfigRecord,
  getMcpCatalog,
  installMcpCatalogEntry,
  keycloakLogin,
  removeMcpServer,
  saveHermesConfig,
  setEnvVar
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, Check, ChevronRight, KeyRound, Loader2, ShieldCheck } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import type { EnvVarInfo, HermesConfigRecord, McpCatalogEntry } from '@/types/hermes'

import { ProviderKeyRows } from './credential-key-ui'
import { SettingsCategoryHeading, useEnvCredentials } from './env-credentials'
import { LoadingState, Pill, SettingsContent } from './primitives'

// Sub-views surfaced as a sidebar subnav: account sign-in, MCP catalog, and raw API keys.
export const PROVIDER_VIEWS = ['accounts', 'catalog', 'keys'] as const

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
      description: 'IAMDS LiteLLM gateway key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: mainInfo.is_set,
      name: 'IAMDS LiteLLM',
      primary: [mainKey, mainInfo],
      priority: 0
    }
  ]

  const stagingInfo = vars.IAMDS_LITELLM_STAGING_API_KEY
  if (stagingInfo) {
    groups.push({
      advanced: [],
      description: 'IAMDS LiteLLM staging key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: stagingInfo.is_set,
      name: 'IAMDS LiteLLM (Staging)',
      primary: ['IAMDS_LITELLM_STAGING_API_KEY', stagingInfo],
      priority: 1
    })
  }

  const devInfo = vars.IAMDS_LITELLM_DEV_API_KEY
  if (devInfo) {
    groups.push({
      advanced: [],
      description: 'IAMDS LiteLLM dev key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: devInfo.is_set,
      name: 'IAMDS LiteLLM (Dev)',
      primary: ['IAMDS_LITELLM_DEV_API_KEY', devInfo],
      priority: 2
    })
  }

  return groups
}

function normalizeProviderBaseUrl(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''
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
  parsed.protocol = 'https:'
  parsed.hash = ''
  const cleaned = `${parsed.origin}${parsed.pathname}${parsed.search}`.replace(/\/+$/, '')
  if (cleaned.endsWith('/litellm/v1')) return cleaned
  if (cleaned.endsWith('/litellm/mcp')) return `${cleaned.slice(0, -'/litellm/mcp'.length)}/litellm/v1`
  return `${cleaned}/litellm/v1`
}

function toEditableBaseUrl(configuredUrl: string): string {
  const trimmed = configuredUrl.trim().replace(/\/+$/, '')
  if (trimmed.endsWith('/litellm/v1')) return trimmed.slice(0, -'/litellm/v1'.length)
  if (trimmed.endsWith('/litellm/mcp')) return trimmed.slice(0, -'/litellm/mcp'.length)
  return trimmed
}

function readProviderBaseUrl(config: Record<string, unknown>, slug: string): string {
  const providers = config.providers
  if (!providers || typeof providers !== 'object' || Array.isArray(providers)) return ''
  const entry = (providers as Record<string, unknown>)[slug]
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return ''
  const baseUrl = (entry as Record<string, unknown>).base_url
  return typeof baseUrl === 'string' ? toEditableBaseUrl(baseUrl) : ''
}

function IamdsExtraProvidersPanel() {
  const [stagingBaseUrl, setStagingBaseUrl] = useState('')
  const [devBaseUrl, setDevBaseUrl] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const config = await getHermesConfigRecord()
        if (!cancelled) {
          setStagingBaseUrl(readProviderBaseUrl(config, 'iamds-litellm-staging'))
          setDevBaseUrl(readProviderBaseUrl(config, 'iamds-litellm-dev'))
        }
      } catch {
        // Best-effort prefill only.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const saveUrls = async () => {
    const stagingNormalized = normalizeProviderBaseUrl(stagingBaseUrl)
    const devNormalized = normalizeProviderBaseUrl(devBaseUrl)

    if (stagingBaseUrl.trim() && !stagingNormalized) {
      notify({ kind: 'error', message: 'Staging URL is invalid', title: 'Could not save provider URLs' })
      return
    }
    if (devBaseUrl.trim() && !devNormalized) {
      notify({ kind: 'error', message: 'Dev URL is invalid', title: 'Could not save provider URLs' })
      return
    }

    setIsSaving(true)
    try {
      const config = await getHermesConfigRecord()
      const nextProvidersRaw = config.providers
      const nextProviders =
        nextProvidersRaw && typeof nextProvidersRaw === 'object' && !Array.isArray(nextProvidersRaw)
          ? { ...(nextProvidersRaw as Record<string, unknown>) }
          : {}

      const upsertOrDelete = (slug: string, name: string, keyEnv: string, baseUrl: string) => {
        if (!baseUrl) {
          delete nextProviders[slug]
          return
        }
        const existingRaw = nextProviders[slug]
        const existing =
          existingRaw && typeof existingRaw === 'object' && !Array.isArray(existingRaw)
            ? { ...(existingRaw as Record<string, unknown>) }
            : {}
        existing.name = name
        existing.base_url = baseUrl
        existing.key_env = keyEnv
        existing.transport = 'codex_responses'
        nextProviders[slug] = existing
      }

      upsertOrDelete('iamds-litellm-staging', 'IAMDS LiteLLM (Staging)', 'IAMDS_LITELLM_STAGING_API_KEY', stagingNormalized)
      upsertOrDelete('iamds-litellm-dev', 'IAMDS LiteLLM (Dev)', 'IAMDS_LITELLM_DEV_API_KEY', devNormalized)

      await saveHermesConfig({ ...config, providers: nextProviders })
      notify({ kind: 'success', message: 'Staging/Dev provider URLs saved', title: 'Saved' })
    } catch (err) {
      notifyError(err, 'Failed to save provider URLs')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="mt-4 rounded-[8px] border border-border bg-muted/20 p-3">
      <h3 className="text-sm font-medium text-foreground">Additional IAMDS providers</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Configure staging/dev endpoint URLs here. API keys are edited above in the provider key rows.
      </p>

      <div className="mt-3 grid gap-3">
        <div className="grid gap-1">
          <label className="text-xs font-medium text-foreground" htmlFor="iamds-staging-url">
            Staging Base URL
          </label>
          <Input
            id="iamds-staging-url"
            onChange={e => setStagingBaseUrl(e.target.value)}
            placeholder="https://staging.suite.example.com"
            value={stagingBaseUrl}
          />
        </div>

        <div className="grid gap-1">
          <label className="text-xs font-medium text-foreground" htmlFor="iamds-dev-url">
            Dev Base URL
          </label>
          <Input
            id="iamds-dev-url"
            onChange={e => setDevBaseUrl(e.target.value)}
            placeholder="https://dev.suite.example.com"
            value={devBaseUrl}
          />
        </div>
      </div>

      <div className="mt-3 flex justify-end">
        <Button disabled={isSaving} onClick={() => void saveUrls()} size="sm">
          {isSaving ? 'Saving…' : 'Save provider URLs'}
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

  const handleKeycloakLogin = async () => {
    setKeycloakError(null)
    setIsKeycloakLoading(true)
    setKeycloakConnected(false)

    const baseUrl = DEFAULT_BASE_URL.trim()
    if (!baseUrl) {
      setKeycloakError('Set VITE_DEFAULT_BASE_URL in the enterprise bundle to enable Keycloak SSO')
      setIsKeycloakLoading(false)
      return
    }

    try {
      const result = await keycloakLogin({
        baseUrl,
        realm: DEFAULT_REALM,
        redirectUri: DEFAULT_REDIRECT_URI || `${baseUrl}/oauth/oidc/callback`,
      })

      await setEnvVar('IAMDS_LITELLM_API_KEY', result.apiKey)
      setKeycloakConnected(true)
      notify({ kind: 'success', message: 'API key obtained via Keycloak SSO', title: 'Connected' })
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
            <span className="text-[length:var(--conversation-text-font-size)] font-semibold">IAMDS LiteLLM</span>
            <span className="inline-flex items-center gap-1.5 bg-primary px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-primary-foreground">
              <span aria-hidden="true" className="dither inline-block size-2 shrink-0" />
              {t.onboarding.recommended}
            </span>
          </div>
          <p className="mb-3 text-xs leading-5 text-muted-foreground">
            Sign in with your organisation account to automatically obtain an API key.
          </p>
          <button
            type="button"
            onClick={() => void handleKeycloakLogin()}
            disabled={isKeycloakLoading}
            className="flex w-full items-center justify-center gap-2 rounded border border-primary bg-primary/10 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
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
              IAMDS LiteLLM
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

  const loadCatalogAndConfig = async () => {
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
    }
  }

  useEffect(() => {
    void loadCatalogAndConfig()
  }, [])

  const openInstallModal = (entry: McpCatalogEntry) => {
    if (entry.disabled) return
    const initialSecrets: Record<string, string> = {}
    const envVars = entry.required_env ?? entry.auth?.env ?? []

    for (const item of envVars) {
      if (item.default) {
        initialSecrets[item.name] = item.default
      } else {
        initialSecrets[item.name] = ''
      }
    }
    setSecretInputs(initialSecrets)
    setInstallModalEntry(entry)
  }

  const handleInstallCatalog = async () => {
    if (!installModalEntry) return

    setInstalling(true)
    try {
      const result = await installMcpCatalogEntry({
        enable: true,
        name: installModalEntry.name,
        secrets: secretInputs
      })

      if (result.ok) {
        notify({
          kind: 'success',
          message: m.catalogInstallSuccessMessage(installModalEntry.name),
          title: m.catalogInstallSuccessTitle
        })
        await loadCatalogAndConfig()
        onRefreshCreds?.()
        setInstallModalEntry(null)
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

  if (catalogEntries.length === 0) {
    return null
  }

  return (
    <section className="mt-6 border-t border-border/50 pt-5">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-foreground">{m.catalogSectionTitle}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{m.catalogSectionDesc}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {catalogEntries.map(entry => {
          const isInstalled = Boolean(installedServers[entry.name])
          const isDisabled = entry.disabled === true

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
                  <Pill>{isDisabled ? 'In Entwicklung' : (entry.source ? entry.source.split(' ')[0] : 'catalog')}</Pill>
                </div>
                <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">{entry.description}</p>
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-border/50 pt-2.5">
                <span className="text-xs text-muted-foreground">{isInstalled ? m.catalogInstalled : ''}</span>
                <div className="flex items-center gap-1.5">
                  {isInstalled && (
                    <Button
                      onClick={() => void handleUninstall(entry.name)}
                      size="xs"
                      variant="ghost"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
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

            <div className="grid gap-3 py-2">
              {(installModalEntry.required_env ?? installModalEntry.auth?.env ?? []).map(item => {
                const isSet = vars[item.name]?.is_set
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
                {Boolean(installedServers[installModalEntry.name]) && (
                  <Button
                    onClick={() => void handleUninstall(installModalEntry.name)}
                    size="xs"
                    variant="ghost"
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
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

  if (view === 'catalog') {
    return (
      <SettingsContent>
        <McpCatalogSection vars={vars} onRefreshCreds={() => void refetch()} />
      </SettingsContent>
    )
  }

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
            <IamdsExtraProvidersPanel />
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
      <IamdsAccountPanel onWantApiKey={() => onViewChange('keys')} onRefreshCreds={() => void refetch()} />
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
