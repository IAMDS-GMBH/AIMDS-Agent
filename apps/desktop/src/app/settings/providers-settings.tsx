import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getHermesConfigRecord, saveHermesConfig } from '@/hermes'
import { useI18n } from '@/i18n'
import { ChevronRight, KeyRound } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'
import type { EnvVarInfo } from '@/types/hermes'

import { ProviderKeyRows } from './credential-key-ui'
import { SettingsCategoryHeading, useEnvCredentials } from './env-credentials'
import { LoadingState, SettingsContent } from './primitives'

// Sub-views surfaced as a sidebar subnav: account sign-in vs raw API keys.
export const PROVIDER_VIEWS = ['accounts', 'keys'] as const

export type ProviderView = (typeof PROVIDER_VIEWS)[number]

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

function IamdsAccountPanel({ onWantApiKey }: { onWantApiKey: () => void }) {
  const { t } = useI18n()

  return (
    <section className="mb-5 grid gap-2">
      <SettingsCategoryHeading icon={KeyRound} title={t.settings.providers.connectAccount} />
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
            <span className="inline-flex items-center gap-1.5 bg-primary px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-primary-foreground">
              <span aria-hidden="true" className="dither inline-block size-2 shrink-0" />
              {t.onboarding.recommended}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Configure your API key to access IAMDS-hosted models
          </p>
        </div>
        <ChevronRight className="size-4 shrink-0 text-primary transition group-hover:translate-x-0.5" />
      </button>
    </section>
  )
}

export function ProvidersSettings({ onViewChange, view }: ProvidersSettingsProps) {
  const { t } = useI18n()
  const { rowProps, vars } = useEnvCredentials()
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
      <IamdsAccountPanel onWantApiKey={() => onViewChange('keys')} />
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
