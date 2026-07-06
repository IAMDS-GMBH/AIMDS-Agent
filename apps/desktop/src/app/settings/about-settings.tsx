import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getHermesConfigRecord, getMcpServers, getRemoteHealthStatus, getStatus, saveHermesConfig } from '@/hermes'
import { type Translations, useI18n } from '@/i18n'
import { CheckCircle2, ExternalLink, Globe, Loader2, RefreshCw, Sparkles } from '@/lib/icons'
import { cn } from '@/lib/utils'
import {
  $desktopVersion,
  $updateApply,
  $updateChecking,
  $updateStatus,
  checkUpdates,
  openUpdatesWindow,
  refreshDesktopVersion
} from '@/store/updates'
import { notify, notifyError } from '@/store/notifications'
import type { McpServerSummary, RemoteHealthResponse, StatusResponse } from '@/types/hermes'

import { ListRow, SectionHeading, SettingsContent } from './primitives'
import { UninstallSection } from './uninstall-section'

const RELEASE_NOTES_URL = 'https://github.com/NousResearch/hermes-agent/releases'

function relativeTime(ms: number | undefined, a: Translations['settings']['about']) {
  if (!ms) {
    return a.never
  }

  const diff = Date.now() - ms

  if (diff < 60_000) {
    return a.justNow
  }

  if (diff < 3_600_000) {
    return a.minAgo(Math.round(diff / 60_000))
  }

  if (diff < 86_400_000) {
    return a.hoursAgo(Math.round(diff / 3_600_000))
  }

  return a.daysAgo(Math.round(diff / 86_400_000))
}

export function AboutSettings() {
  const { t } = useI18n()
  const a = t.settings.about
  const version = useStore($desktopVersion)
  const status = useStore($updateStatus)
  const apply = useStore($updateApply)
  const checking = useStore($updateChecking)
  const [justChecked, setJustChecked] = useState(false)
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

  // The version atom is loaded once at app boot, which makes About show a
  // stale number after a self-update (the running binary is current, the
  // displayed string is not). Re-read on mount so opening About always
  // reflects the running build.
  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

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
    void refreshRemoteHealth()
  }, [])

  useEffect(() => {
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

  const behind = status?.behind ?? 0
  const supported = status?.supported !== false
  const applying = apply.applying || apply.stage === 'restart'

  const handleCheck = async () => {
    setJustChecked(false)
    const next = await checkUpdates()
    setJustChecked(Boolean(next))
  }

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

  let statusLine: string
  let statusTone: 'idle' | 'available' | 'error' = 'idle'

  if (!supported) {
    statusLine = status?.message ?? a.cantUpdate
    statusTone = 'error'
  } else if (status?.error) {
    statusLine = a.cantReach
    statusTone = 'error'
  } else if (applying) {
    statusLine = a.installing
    statusTone = 'available'
  } else if (behind > 0) {
    statusLine = a.updateReady(behind)
    statusTone = 'available'
  } else if (status) {
    statusLine = a.onLatest
  } else {
    statusLine = a.tapCheck
  }

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
  const enabledMcpServers = mcpServers.filter(server => server.enabled)
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

  return (
    <SettingsContent>
      <div className="flex flex-col items-center gap-3 pt-6 pb-2 text-center">
        <BrandMark className="size-16" />
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{a.heading}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {version?.appVersion ? a.version(version.appVersion) : a.versionUnavailable}
          </p>
        </div>
      </div>

      <div className="mx-auto mt-4 w-full max-w-2xl">
        <SectionHeading icon={RefreshCw} title={a.updates} />

        <div
          className={cn(
            'rounded-xl border px-4 py-3 text-sm',
            statusTone === 'available' && 'border-primary/30 bg-primary/5 text-foreground',
            statusTone === 'error' && 'border-destructive/35 bg-destructive/5 text-destructive',
            statusTone === 'idle' && 'border-border/70 bg-muted/20 text-foreground'
          )}
        >
          <div className="flex items-start gap-2">
            {statusTone === 'available' ? (
              <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
            ) : statusTone === 'error' ? null : (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            )}
            <div className="min-w-0">
              <p className="font-medium">{statusLine}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {a.lastChecked(relativeTime(status?.fetchedAt, a))}
                {justChecked && !checking ? a.justNowSuffix : ''}
              </p>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-4">
            <Button
              disabled={checking || applying || !supported}
              onClick={() => void handleCheck()}
              size="sm"
              variant="textStrong"
            >
              {checking ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
              {checking ? a.checking : a.checkNow}
            </Button>

            {behind > 0 && supported && !applying && (
              <Button onClick={() => openUpdatesWindow()} size="sm">
                {a.seeWhatsNew}
              </Button>
            )}

            <Button
              disabled={sendingSupportLogs || !supportConfigured}
              onClick={() => void handleSendSupportLogs()}
              size="sm"
              variant="text"
            >
              {sendingSupportLogs ? 'Sending support logs…' : 'Send support logs'}
            </Button>

            <Button asChild className="ml-auto" size="sm" variant="text">
              <a
                href={RELEASE_NOTES_URL}
                onClick={event => {
                  event.preventDefault()
                  void window.hermesDesktop?.openExternal?.(RELEASE_NOTES_URL)
                }}
                rel="noreferrer"
                target="_blank"
              >
                <ExternalLink className="size-3" />
                {a.releaseNotes}
              </a>
            </Button>
          </div>
          {!supportConfigured && supportConfigLoaded && (
            <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
              Support logs are not configured yet. Set upload URL and API key below.
            </p>
          )}
          <div className="mt-3 grid gap-2 rounded-lg border border-border/70 bg-background/40 p-3">
            <p className="text-xs font-medium">Support log upload settings</p>
            <Input
              onChange={event => setSupportUploadUrl(event.target.value)}
              placeholder="https://support.example.com/api/log-upload"
              value={supportUploadUrl}
            />
            <Input
              onChange={event => setSupportApiKey(event.target.value)}
              placeholder={supportKeyConfigured ? 'API key already set (enter to replace)' : 'Bearer API key'}
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

        <ListRow
          description={a.automaticUpdatesDesc}
          hint={a.branchCommit(status?.branch ?? 'unknown', status?.currentSha?.slice(0, 7) ?? 'unknown')}
          title={a.automaticUpdates}
        />

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

        <UninstallSection />
      </div>
    </SettingsContent>
  )
}
