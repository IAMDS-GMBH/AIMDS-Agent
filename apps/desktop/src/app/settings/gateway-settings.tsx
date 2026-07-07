import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getHermesConfigRecord, getMcpServers, getRemoteHealthStatus, getStatus, saveHermesConfig } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, FileText, Globe, Loader2, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $profiles, refreshActiveProfile } from '@/store/profile'
import type { McpServerSummary, RemoteHealthResponse, StatusResponse } from '@/types/hermes'

import { EmptyState, ListRow, LoadingState, Pill, SectionHeading, SettingsContent } from './primitives'

interface GatewaySettingsState {
  envOverride: boolean
}

const EMPTY_STATE: GatewaySettingsState = {
  envOverride: false
}

function ScopeChip({ active, label, onSelect }: { active: boolean; label: string; onSelect: () => void }) {
  return (
    <button
      className={[
        'rounded-full border px-3 py-1 text-[length:var(--conversation-caption-font-size)] transition',
        active
          ? 'border-(--ui-stroke-secondary) bg-(--ui-bg-tertiary) text-(--ui-text-primary)'
          : 'border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)'
      ].join(' ')}
      onClick={onSelect}
      type="button"
    >
      {label}
    </button>
  )
}

export function GatewaySettings() {
  const { t } = useI18n()
  const g = t.settings.gateway
  const [loading, setLoading] = useState(true)
  const [state, setState] = useState<GatewaySettingsState>(EMPTY_STATE)
  const [scope, setScope] = useState<null | string>(null)
  const profiles = useStore($profiles)

  const [remoteHealth, setRemoteHealth] = useState<null | RemoteHealthResponse>(null)
  const [remoteHealthLoading, setRemoteHealthLoading] = useState(false)
  const [localStatus, setLocalStatus] = useState<null | StatusResponse>(null)
  const [mcpServers, setMcpServers] = useState<McpServerSummary[]>([])
  const [localConnectivityLoading, setLocalConnectivityLoading] = useState(false)
  const [localConnectivityError, setLocalConnectivityError] = useState('')
  const [localCheckedAt, setLocalCheckedAt] = useState<null | number>(null)
  const [sendingSupportLogs, setSendingSupportLogs] = useState(false)
  const [supportUploadUrl, setSupportUploadUrl] = useState('')
  const [supportApiKey, setSupportApiKey] = useState('')
  const [supportConfigLoaded, setSupportConfigLoaded] = useState(false)
  const [savingSupportConfig, setSavingSupportConfig] = useState(false)
  const [supportKeyConfigured, setSupportKeyConfigured] = useState(false)

  const refreshRemoteHealth = async () => {
    setRemoteHealthLoading(true)
    try {
      const payload = await getRemoteHealthStatus()
      setRemoteHealth(payload)
    } finally {
      setRemoteHealthLoading(false)
    }
  }

  const refreshLocalConnectivity = async () => {
    setLocalConnectivityLoading(true)
    setLocalConnectivityError('')
    try {
      const [statusPayload, mcpPayload] = await Promise.all([getStatus(), getMcpServers()])
      setLocalStatus(statusPayload)
      setMcpServers(mcpPayload.servers ?? [])
      setLocalCheckedAt(Date.now())
    } catch (error) {
      setLocalConnectivityError(error instanceof Error ? error.message : String(error))
      setLocalCheckedAt(Date.now())
    } finally {
      setLocalConnectivityLoading(false)
    }
  }

  useEffect(() => {
    void refreshActiveProfile()
  }, [])

  useEffect(() => {
    let cancelled = false
    const desktop = window.hermesDesktop

    if (!desktop?.getConnectionConfig) {
      setLoading(false)
      return () => void (cancelled = true)
    }

    setLoading(true)

    desktop
      .getConnectionConfig(scope)
      .then(config => {
        if (!cancelled) {
          setState({ envOverride: config.envOverride })
        }
      })
      .catch(err => notifyError(err, g.failedLoad))
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => void (cancelled = true)
  }, [scope, g.failedLoad])

  useEffect(() => {
    void refreshRemoteHealth()
    void refreshLocalConnectivity()
  }, [])

  useEffect(() => {
    let cancelled = false
    void getHermesConfigRecord()
      .then(config => {
        if (cancelled) {
          return
        }
        const support = (config?.support && typeof config.support === 'object'
          ? (config.support as Record<string, unknown>)
          : {}) as Record<string, unknown>
        setSupportUploadUrl(typeof support.upload_url === 'string' ? support.upload_url : '')
        const existingKey = typeof support.api_key === 'string' ? support.api_key : ''
        setSupportKeyConfigured(existingKey.trim().length > 0)
        setSupportConfigLoaded(true)
      })
      .catch(() => {
        if (!cancelled) {
          setSupportConfigLoaded(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  const handleSendSupportLogs = async () => {
    setSendingSupportLogs(true)
    try {
      const result = await window.hermesDesktop?.sendSupportLogs?.({ reason: 'on_demand_settings' })
      if (result?.ok) {
        const reference = result.reference_id || result.referenceId
        notify({
          kind: 'success',
          title: 'Support logs sent',
          message: reference ? `Reference: ${reference}` : 'Diagnostic logs were uploaded for support.'
        })
      } else {
        notify({
          kind: 'warning',
          title: 'Support log upload failed',
          message: result?.error || 'Could not upload support logs.'
        })
      }
    } catch (err) {
      notifyError(err, 'Support log upload failed')
    } finally {
      setSendingSupportLogs(false)
    }
  }

  const saveSupportConfig = async () => {
    setSavingSupportConfig(true)
    try {
      const config = await getHermesConfigRecord()
      const currentSupport =
        config?.support && typeof config.support === 'object' ? (config.support as Record<string, unknown>) : {}
      const nextApiKey = supportApiKey.trim() ? supportApiKey.trim() : String(currentSupport.api_key ?? '')
      const nextConfig = {
        ...config,
        support: {
          ...currentSupport,
          upload_url: supportUploadUrl.trim(),
          api_key: nextApiKey
        }
      }
      await saveHermesConfig(nextConfig)
      setSupportKeyConfigured(nextApiKey.length > 0)
      setSupportApiKey('')
      notify({
        kind: 'success',
        title: 'Support endpoint saved',
        message: 'Support upload URL and API key settings were saved.'
      })
    } catch (err) {
      notifyError(err, 'Could not save support settings')
    } finally {
      setSavingSupportConfig(false)
    }
  }

  const namedProfiles = useMemo(() => profiles.filter(profile => profile.name !== 'default'), [profiles])
  const enabledMcpServers = mcpServers.filter(server => server.enabled)
  const remoteSeverity = remoteHealth?.severity ?? (remoteHealth?.ok ? 'healthy' : 'critical')
  const remoteToneClass =
    remoteSeverity === 'critical'
      ? 'border-destructive/35 bg-destructive/5 text-destructive'
      : remoteSeverity === 'warning'
        ? 'border-amber-500/35 bg-amber-500/10 text-foreground'
        : 'border-border/70 bg-muted/20 text-foreground'
  const remoteChecked = remoteHealth?.checked_at
    ? new Date(remoteHealth.checked_at).toLocaleString()
    : 'n/a'
  const localSeverity = localConnectivityError
    ? 'critical'
    : localStatus
      ? 'healthy'
      : 'warning'
  const localToneClass =
    localSeverity === 'critical'
      ? 'border-destructive/35 bg-destructive/5 text-destructive'
      : localSeverity === 'warning'
        ? 'border-amber-500/35 bg-amber-500/10 text-foreground'
        : 'border-border/70 bg-muted/20 text-foreground'
  const localChecked = localCheckedAt ? new Date(localCheckedAt).toLocaleString() : 'n/a'
  const supportConfigured = supportUploadUrl.trim().length > 0 && (supportKeyConfigured || supportApiKey.trim().length > 0)

  if (loading) {
    return <LoadingState label={g.loading} />
  }

  if (!window.hermesDesktop?.getConnectionConfig) {
    return (
      <EmptyState
        description={g.unavailableDesc}
        title={g.unavailableTitle}
      />
    )
  }

  return (
    <SettingsContent>
      <div className="mb-5">
        <div className="flex items-center gap-2 text-[length:var(--conversation-text-font-size)] font-medium">
          <Globe className="size-4 text-muted-foreground" />
          {g.title}
          {state.envOverride ? <Pill tone="primary">{g.envOverride}</Pill> : null}
        </div>
        <p className="mt-2 max-w-2xl text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
          {g.localDesc}
        </p>
      </div>

      {namedProfiles.length > 0 ? (
        <div className="mb-5 grid gap-2">
          <div className="text-[length:var(--conversation-caption-font-size)] font-medium text-(--ui-text-secondary)">
            {g.appliesTo}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <ScopeChip active={scope === null} label={g.allProfiles} onSelect={() => setScope(null)} />
            {namedProfiles.map(profile => (
              <ScopeChip
                active={scope === profile.name}
                key={profile.name}
                label={profile.name}
                onSelect={() => setScope(profile.name)}
              />
            ))}
          </div>
          <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
            {scope === null ? g.defaultConnection : g.profileConnection(scope)}
          </p>
        </div>
      ) : null}

      {state.envOverride ? (
        <div className="mb-5 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-[length:var(--conversation-caption-font-size)] text-destructive">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <div>
            <div className="font-medium">{g.envOverrideTitle}</div>
            <div className="mt-1 leading-5">{g.envOverrideDesc}</div>
          </div>
        </div>
      ) : null}

      <div className="mt-2 grid gap-1">
        <ListRow
          action={
            <Button onClick={() => void window.hermesDesktop?.revealLogs()} size="sm" variant="textStrong">
              <FileText />
              {g.openLogs}
            </Button>
          }
          description={g.diagnosticsDesc}
          title={g.diagnostics}
        />
      </div>

      <div className="mt-5">
        <SectionHeading icon={Globe} title="Local connectivity" />
        <div className={cn('rounded-xl border px-4 py-3 text-sm', localToneClass)}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium">
                {localConnectivityError
                  ? `Status: ${localConnectivityError}`
                  : localStatus
                    ? 'Status: healthy'
                    : 'Status: unknown'}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Endpoint: Hermes local API ({localStatus?.gateway_health_url ?? 'embedded'})
              </p>
              <p className="mt-1 text-xs text-muted-foreground">Checked at: {localChecked}</p>
            </div>
            <Button
              disabled={localConnectivityLoading}
              onClick={() => void refreshLocalConnectivity()}
              size="sm"
              variant="text"
            >
              {localConnectivityLoading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
              {localConnectivityLoading ? 'Checking…' : 'Refresh'}
            </Button>
          </div>

          {localStatus && !localConnectivityError && (
            <div className="mt-3 grid gap-1">
              <p className="text-xs font-medium text-foreground">Runtime checks</p>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">Hermes local API</span>
                <span className="text-emerald-600 dark:text-emerald-400">up</span>
              </div>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">Gateway process</span>
                <span className={cn(localStatus.gateway_running ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground')}>
                  {localStatus.gateway_running ? 'up' : 'optional (not running)'}
                </span>
              </div>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">Dashboard auth gate</span>
                <span>{localStatus.auth_required ? `enabled (${(localStatus.auth_providers ?? []).join(', ') || 'configured'})` : 'disabled'}</span>
              </div>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">Configured MCP servers</span>
                <span>{enabledMcpServers.length}/{mcpServers.length}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-5">
        <SectionHeading icon={Globe} title="Remote connectivity" />
        <div className={cn('rounded-xl border px-4 py-3 text-sm', remoteToneClass)}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium">
                {remoteHealth?.ok
                  ? `Status: ${remoteHealth.overall_status ?? 'unknown'}`
                  : (remoteHealth?.error ?? 'Remote health check failed')}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {remoteHealth?.health_url ? `Endpoint: ${remoteHealth.health_url}` : 'Endpoint: not configured'}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">Checked at: {remoteChecked}</p>
            </div>
            <Button disabled={remoteHealthLoading} onClick={() => void refreshRemoteHealth()} size="sm" variant="text">
              {remoteHealthLoading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
              {remoteHealthLoading ? 'Checking…' : 'Refresh'}
            </Button>
          </div>

          {remoteHealth?.ok && (
            <div className="mt-3 grid gap-1">
              <p className="text-xs font-medium text-foreground">Critical services</p>
              {(remoteHealth.critical_services ?? []).map(service => (
                <div
                  className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs"
                  key={`${service.name}-${service.tier}`}
                >
                  <span className="truncate">{service.name}</span>
                  <span className={cn(service.is_up ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive')}>
                    {service.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-5 rounded-xl border border-border/70 bg-muted/20 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">Support</p>
          <Button
            disabled={sendingSupportLogs || !supportConfigured}
            onClick={() => void handleSendSupportLogs()}
            size="sm"
            variant="text"
          >
            {sendingSupportLogs ? 'Sending support logs…' : 'Send support logs'}
          </Button>
        </div>
        {!supportConfigured && supportConfigLoaded && (
          <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
            Support logs are not configured yet. Set upload URL and API key below.
          </p>
        )}
        <div className="grid gap-2 rounded-lg border border-border/70 bg-background/40 p-3">
          <p className="text-xs font-medium">Support log upload settings</p>
          <Input
            onChange={event => setSupportUploadUrl(event.target.value)}
            placeholder="https://support.example.com/api/log-upload"
            value={supportUploadUrl}
          />
          <Input
            onChange={event => setSupportApiKey(event.target.value)}
            placeholder={supportKeyConfigured ? 'API key already set (enter to replace)' : '****** key'}
            type="password"
            value={supportApiKey}
          />
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{supportKeyConfigured ? 'API key is currently configured.' : 'API key not configured.'}</span>
            <Button
              disabled={savingSupportConfig || !supportUploadUrl.trim() || (!supportKeyConfigured && !supportApiKey.trim())}
              onClick={() => void saveSupportConfig()}
              size="sm"
            >
              {savingSupportConfig ? 'Saving…' : 'Save support settings'}
            </Button>
          </div>
        </div>
      </div>
    </SettingsContent>
  )
}
