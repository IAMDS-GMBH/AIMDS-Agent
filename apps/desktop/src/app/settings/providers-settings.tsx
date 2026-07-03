import { useState } from 'react'

import { useI18n } from '@/i18n'
import { ChevronRight, KeyRound } from '@/lib/icons'
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
  if (stagingInfo?.is_set) {
    groups.push({
      advanced: [],
      description: 'IAMDS LiteLLM staging key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: true,
      name: 'IAMDS LiteLLM (Staging)',
      primary: ['IAMDS_LITELLM_STAGING_API_KEY', stagingInfo],
      priority: 1
    })
  }

  const devInfo = vars.IAMDS_LITELLM_DEV_API_KEY
  if (devInfo?.is_set) {
    groups.push({
      advanced: [],
      description: 'IAMDS LiteLLM dev key from ~/.hermes/.env',
      docsUrl: '',
      hasAnySet: true,
      name: 'IAMDS LiteLLM (Dev)',
      primary: ['IAMDS_LITELLM_DEV_API_KEY', devInfo],
      priority: 2
    })
  }

  return groups
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
