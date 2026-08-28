import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Tip } from '@/components/ui/tooltip'
import { getHermesConfigRecord, getMcpServers, getRemoteHealthStatus, getStatus, reloadMcpServers, restartGateway } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, Globe, Loader2, RefreshCw } from '@/lib/icons'
import { getMcpServerToolCount } from '@/lib/mcp-helpers'
import { formatAimdsProviderLabel } from '@/lib/model-status-label'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $profiles, refreshActiveProfile } from '@/store/profile'
import type { McpServerSummary, RemoteHealthResponse, StatusResponse } from '@/types/hermes'

import { EmptyState, LoadingState, SectionHeading, SettingsContent } from './primitives'

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

interface TicketStatusData {
  status?: string
  case_status?: string
  case_summary?: string
  resolution_note?: string
}

export function SystemStatusContent() {
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

  const [aimdsEnv, setAimdsEnv] = useState<string | null>(null)
  const [restartingGateway, setRestartingGateway] = useState(false)
  const [reloadingMcp, setReloadingMcp] = useState(false)

  const handleReloadMcp = async () => {
    setReloadingMcp(true)

    try {
      const res = await reloadMcpServers()
      notify({
        kind: 'success',
        title: 'MCP-Server',
        message: res.message || `${res.reloaded} MCP-Server erfolgreich neu geladen.`
      })
      await refreshLocalConnectivity()
    } catch (err) {
      notifyError(err, 'MCP-Server konnten nicht neu geladen werden')
    } finally {
      setReloadingMcp(false)
    }
  }

  const handleRestartGateway = async () => {
    setRestartingGateway(true)

    try {
      await restartGateway()
      notify({
        kind: 'success',
        title: 'Gateway Neustart',
        message: 'Der Hermes Gateway-Prozess wird neu gestartet...'
      })
      setTimeout(() => {
        void refreshLocalConnectivity()
        setRestartingGateway(false)
      }, 2500)
    } catch (err) {
      notifyError(err, 'Gateway konnte nicht neu gestartet werden')
      setRestartingGateway(false)
    }
  }

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

        const rawProvider =
          typeof config?.provider === 'string'
            ? config.provider
            : typeof (config?.model as Record<string, unknown>)?.provider === 'string'
              ? ((config.model as Record<string, unknown>).provider as string)
              : ''

        setAimdsEnv(formatAimdsProviderLabel(rawProvider))
      })
      .catch(() => {})

    return () => {
      cancelled = true
    }
  }, [])

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
    <div className="grid gap-5">
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

      {/* 3. LOKALE VERBINDUNG & STATUS */}
      <div className="mt-5">
        <SectionHeading icon={Globe} title={g.localConnTitle} />
        <div className={cn('rounded-xl border px-4 py-3 text-sm', localToneClass)}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium">
                {localConnectivityError
                  ? g.statusError(localConnectivityError)
                  : localStatus
                    ? g.statusHealthy
                    : g.statusUnknown}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {g.endpointLocal(localStatus?.gateway_health_url ?? 'embedded')}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {g.checkedAt(localChecked)}
                {localStatus?.started_at ? ` • Gestartet: ${new Date(localStatus.started_at * 1000).toLocaleString()}` : ''}
                {localStatus?.uptime_seconds !== undefined && localStatus?.uptime_seconds !== null
                  ? ` (Laufzeit: ${Math.floor(localStatus.uptime_seconds / 60)}m ${Math.floor(localStatus.uptime_seconds % 60)}s)`
                  : ''}
              </p>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={reloadingMcp}
                onClick={() => void handleReloadMcp()}
                size="sm"
                variant="text"
              >
                {reloadingMcp ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
                {reloadingMcp ? 'MCPs laden...' : 'MCP neu laden'}
              </Button>
              <Button
                disabled={restartingGateway}
                onClick={() => void handleRestartGateway()}
                size="sm"
                variant="text"
              >
                {restartingGateway ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
                {restartingGateway ? 'Neu starten...' : 'Gateway neu starten'}
              </Button>
              <Button
                disabled={localConnectivityLoading}
                onClick={() => void refreshLocalConnectivity()}
                size="sm"
                variant="text"
              >
                {localConnectivityLoading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
                {localConnectivityLoading ? g.checking : g.refresh}
              </Button>
            </div>
          </div>

          {localStatus && !localConnectivityError && (
            <div className="mt-3 grid gap-1">
              <p className="text-xs font-medium text-foreground">{g.runtimeChecks}</p>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">{g.hermesLocalApi}</span>
                <span className="text-emerald-600 dark:text-emerald-400">{g.statusUp}</span>
              </div>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">{g.gatewayProcess}</span>
                <span className={cn(localStatus.gateway_running ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground')}>
                  {localStatus.gateway_running ? g.statusUp : g.gatewayOptional}
                </span>
              </div>
              <div className="flex items-center justify-between rounded border border-border/60 bg-background/40 px-2 py-1 text-xs">
                <span className="truncate">{g.dashboardAuthGate}</span>
                <span>{localStatus.auth_required ? g.authGateEnabled((localStatus.auth_providers ?? []).join(', ') || 'configured') : g.authGateDisabled}</span>
              </div>
              <div className="rounded border border-border/60 bg-background/40 p-2 text-xs">
                <div className="flex items-center justify-between font-medium">
                  <span className="truncate">{g.configuredMcp}</span>
                  <div className="flex items-center gap-2">
                    <Button
                      className="h-5 px-1.5 text-[10px]"
                      disabled={reloadingMcp}
                      onClick={() => void handleReloadMcp()}
                      size="xs"
                      variant="outline"
                    >
                      {reloadingMcp ? <Loader2 className="mr-1 size-2.5 animate-spin" /> : <RefreshCw className="mr-1 size-2.5" />}
                      Neu laden
                    </Button>
                    <span className="font-mono text-muted-foreground">
                      {enabledMcpServers.length}/{mcpServers.length}
                      {mcpServers.some(s => getMcpServerToolCount(s) !== null)
                        ? ` (${enabledMcpServers.reduce((sum, s) => sum + (getMcpServerToolCount(s) ?? 0), 0)} Tools)`
                        : ''}
                    </span>
                  </div>
                </div>
                {mcpServers.length > 0 && (
                  <div className="mt-2 grid gap-1 border-t border-border/40 pt-1.5">
                    {mcpServers.map(server => {
                      const count = getMcpServerToolCount(server)
                      const countLabel = count !== null ? `${count} Tool${count === 1 ? '' : 's'}` : null

                      const toolsList = server.discovered_tools && server.discovered_tools.length > 0
                        ? server.discovered_tools.join(', ')
                        : null

                      const badge = (
                        <span
                          className={cn(
                            'font-mono cursor-help',
                            server.enabled ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
                          )}
                        >
                          {server.enabled
                            ? countLabel
                              ? `${g.statusUp} (${countLabel})`
                              : g.statusUp
                            : g.gatewayOptional}
                        </span>
                      )

                      return (
                        <div className="flex items-center justify-between pl-2 text-[11px]" key={server.name}>
                          <span className="truncate font-mono text-foreground/80">{server.name}</span>
                          {toolsList ? (
                            <Tip label={`Tools (${server.discovered_tools?.length}): ${toolsList}`}>
                              {badge}
                            </Tip>
                          ) : (
                            badge
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 4. REMOTE-VERBINDUNG & STATUS */}
      <div className="mt-5">
        <SectionHeading icon={Globe} title={g.remoteConnTitle} />
        <div className={cn('rounded-xl border px-4 py-3 text-sm', remoteToneClass)}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium">
                {remoteHealth?.ok
                  ? g.statusError(remoteHealth.overall_status ?? 'unknown')
                  : (remoteHealth?.error ?? g.remoteHealthFailed)}
              </p>
              {aimdsEnv && (
                <p className="mt-0.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                  {g.environmentLabel(aimdsEnv)}
                </p>
              )}
              <p className="mt-1 text-xs text-muted-foreground">
                {remoteHealth?.health_url ? g.endpointRemote(remoteHealth.health_url) : g.endpointNotConfigured}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{g.checkedAt(remoteChecked)}</p>
            </div>
            <Button disabled={remoteHealthLoading} onClick={() => void refreshRemoteHealth()} size="sm" variant="text">
              {remoteHealthLoading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
              {remoteHealthLoading ? g.checking : g.refresh}
            </Button>
          </div>

          {remoteHealth?.ok && (
            <div className="mt-3 grid gap-1">
              <p className="text-xs font-medium text-foreground">{g.criticalServices}</p>
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
    </div>
  )
}

export function GatewaySettings() {
  return (
    <SettingsContent>
      <SystemStatusContent />
    </SettingsContent>
  )
}
