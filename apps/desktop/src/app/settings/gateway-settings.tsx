import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { ReportIssueDialog } from '@/components/report-issue-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tip } from '@/components/ui/tooltip'
import { getHermesConfigRecord, getMcpServers, getRemoteHealthStatus, getStatus, restartGateway, saveHermesConfig } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, Globe, HelpCircle, Loader2, RefreshCw, Trash2 } from '@/lib/icons'
import { getMcpServerToolCount } from '@/lib/mcp-helpers'
import { formatAimdsProviderLabel } from '@/lib/model-status-label'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $profiles, refreshActiveProfile } from '@/store/profile'
import {
  $supportTickets,
  addSupportTicket,
  clearResolvedSupportTickets,
  clearSupportTickets,
  removeSupportTicket,
  updateAndCleanupSupportTickets
} from '@/store/support-tickets'
import type { McpServerSummary, RemoteHealthResponse, StatusResponse } from '@/types/hermes'

import { EmptyState, LoadingState, Pill, SectionHeading, SettingsContent } from './primitives'

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

export function GatewaySettings() {
  const { t } = useI18n()
  const g = t.settings.gateway
  const [loading, setLoading] = useState(true)
  const [state, setState] = useState<GatewaySettingsState>(EMPTY_STATE)
  const [scope, setScope] = useState<null | string>(null)
  const profiles = useStore($profiles)
  const tickets = useStore($supportTickets)

  const [remoteHealth, setRemoteHealth] = useState<null | RemoteHealthResponse>(null)
  const [remoteHealthLoading, setRemoteHealthLoading] = useState(false)
  const [localStatus, setLocalStatus] = useState<null | StatusResponse>(null)
  const [mcpServers, setMcpServers] = useState<McpServerSummary[]>([])
  const [localConnectivityLoading, setLocalConnectivityLoading] = useState(false)
  const [localConnectivityError, setLocalConnectivityError] = useState('')
  const [localCheckedAt, setLocalCheckedAt] = useState<null | number>(null)

  const [sendingSupportLogs, setSendingSupportLogs] = useState(false)
  const [reportIssueOpen, setReportIssueOpen] = useState(false)
  const [supportUploadUrl, setSupportUploadUrl] = useState('')
  const [supportConfigLoaded, setSupportConfigLoaded] = useState(false)
  const [supportKeyConfigured, setSupportKeyConfigured] = useState(false)
  const [aimdsEnv, setAimdsEnv] = useState<string | null>(null)
  const [restartingGateway, setRestartingGateway] = useState(false)

  const [ticketStatusMap, setTicketStatusMap] = useState<Record<string, TicketStatusData>>({})
  const [refreshingTickets, setRefreshingTickets] = useState(false)

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

  const refreshTicketStatuses = async () => {
    if (tickets.length === 0) return
    setRefreshingTickets(true)
    const baseUrl = supportUploadUrl.trim()
      ? supportUploadUrl.trim().replace(/\/upload\/?$/, '')
      : 'https://suite-support.iamds.com/api/v1'

    const newMap: Record<string, TicketStatusData> = { ...ticketStatusMap }
    await Promise.all(
      tickets.map(async item => {
        try {
          const res = await fetch(`${baseUrl}/jobs/${item.jobId}`)
          if (res.ok) {
            const data = await res.json()
            newMap[item.jobId] = data
          }
        } catch {
          // ignore fetch error
        }
      })
    )
    setTicketStatusMap(newMap)
    updateAndCleanupSupportTickets(newMap)
    setRefreshingTickets(false)
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
    if (tickets.length > 0) {
      void refreshTicketStatuses()
    }
  }, [tickets.length])

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
        addSupportTicket({
          jobId: reference || `job-${Date.now()}`,
          referenceId: reference,
          summary: 'Support-Logs gesendet',
          category: 'support_logs',
          createdAt: Date.now()
        })
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
  const supportConfigured = supportUploadUrl.trim().length > 0 || supportKeyConfigured

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

  const getStatusBadge = (itemJobId: string) => {
    const live = ticketStatusMap[itemJobId]
    const statusText = live?.case_status || live?.status || 'QUEUED'

    if (statusText === 'RESOLVED' || statusText === 'COMPLETED') {
      return (
        <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
          {g.statusLabels?.resolved || 'Behoben'}
        </span>
      )
    }
    if (statusText === 'OPEN' || statusText === 'PROCESSING') {
      return (
        <span className="rounded-full bg-blue-500/15 px-2 py-0.5 text-[10px] font-semibold text-blue-700 dark:text-blue-300">
          {g.statusLabels?.processing || 'In Bearbeitung'}
        </span>
      )
    }
    if (statusText === 'FAILED') {
      return (
        <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-[10px] font-semibold text-destructive">
          {g.statusLabels?.failed || 'Fehlgeschlagen'}
        </span>
      )
    }
    return (
      <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
        {g.statusLabels?.queued || 'Warteschlange'}
      </span>
    )
  }

  return (
    <SettingsContent>
      {/* 1. SUPPORT & DIAGNOSE-LOGS (ALWAYS FIRST AT TOP OF DIAGNOSTICS) */}
      <div className="rounded-xl border border-border/70 bg-muted/20 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold">{g.supportTitle}</p>
            <p className="text-xs text-muted-foreground">{g.supportDesc}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={() => setReportIssueOpen(true)}
              size="sm"
              variant="default"
            >
              <HelpCircle className="mr-1.5 size-3.5" />
              {g.reportIssueButton || 'Problem melden'}
            </Button>
            <Button
              disabled={sendingSupportLogs || !supportConfigured}
              onClick={() => void handleSendSupportLogs()}
              size="sm"
              variant="outline"
            >
              {sendingSupportLogs ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
              {sendingSupportLogs ? g.sendingSupportLogs : g.sendSupportLogs}
            </Button>
          </div>
        </div>

        {!supportConfigured && supportConfigLoaded && (
          <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
            {g.supportNotConfigured}
          </p>
        )}

        {/* Support Ticket Status & History Tracker */}
        <div className="rounded-lg border border-border/70 bg-background/40 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div>
              <p className="text-xs font-semibold">{g.supportTicketsTitle || 'Support-Tickets Status & Verlauf'}</p>
              <p className="text-[11px] text-muted-foreground">{g.supportTicketsDesc || 'Übersicht Ihrer Support-Fälle.'}</p>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={refreshingTickets || tickets.length === 0}
                onClick={() => void refreshTicketStatuses()}
                size="sm"
                variant="text"
              >
                {refreshingTickets ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
                {g.supportTicketRefresh || 'Aktualisieren'}
              </Button>
              {tickets.length > 0 && (
                <Button
                  onClick={() => clearResolvedSupportTickets(ticketStatusMap)}
                  size="sm"
                  variant="text"
                >
                  {g.supportTicketClearResolved || 'Gelöste bereinigen'}
                </Button>
              )}
              {tickets.length > 0 && (
                <Button
                  onClick={() => clearSupportTickets()}
                  size="sm"
                  variant="text"
                >
                  <Trash2 className="size-3 text-destructive" />
                  {g.supportTicketClear || 'Leeren'}
                </Button>
              )}
            </div>
          </div>

          {tickets.length === 0 ? (
            <p className="py-2 text-center text-xs text-muted-foreground">
              {g.supportTicketNoTickets || 'Noch keine Support-Tickets gemeldet.'}
            </p>
          ) : (
            <div className="grid gap-2 pt-1">
              {tickets.map(item => {
                const live = ticketStatusMap[item.jobId]
                const isFeedback = item.category === 'feature_request'
                const typePrefix = isFeedback ? 'Feedback' : 'Problemmeldung'
                const displayTitle = item.summary ? `${typePrefix}: ${item.summary}` : (item.referenceId || item.jobId)

                return (
                  <div
                    className="flex flex-col gap-1 rounded-md border border-border/60 bg-background/60 p-2.5 text-xs"
                    key={item.jobId}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <span className="font-semibold text-foreground truncate">
                          {displayTitle}
                        </span>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          ID: {item.referenceId || item.jobId}
                        </span>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="text-[10px] text-muted-foreground">
                          {new Date(item.createdAt).toLocaleString()}
                        </span>
                        {getStatusBadge(item.jobId)}
                        <Button
                          className="h-5 w-5 p-0 text-muted-foreground hover:text-destructive"
                          onClick={() => removeSupportTicket(item.jobId)}
                          size="sm"
                          variant="text"
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      </div>
                    </div>
                    {live?.resolution_note && (
                      <div className="mt-1 rounded bg-emerald-500/10 p-2 text-[11px] text-emerald-800 dark:text-emerald-200">
                        <span className="font-semibold">{g.supportTicketHeaderResolution || 'Lösungs-Hinweis'}: </span>
                        {live.resolution_note}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <ReportIssueDialog onOpenChange={setReportIssueOpen} open={reportIssueOpen} />

      {/* 2. DIAGNOSE & CONNECTIVITY TITLE */}
      <div className="mb-5 mt-6">
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
                  <span className="font-mono text-muted-foreground">
                    {enabledMcpServers.length}/{mcpServers.length}
                    {mcpServers.some(s => getMcpServerToolCount(s) !== null)
                      ? ` (${enabledMcpServers.reduce((sum, s) => sum + (getMcpServerToolCount(s) ?? 0), 0)} Tools)`
                      : ''}
                  </span>
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
                        <div key={server.name} className="flex items-center justify-between pl-2 text-[11px]">
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
    </SettingsContent>
  )
}
