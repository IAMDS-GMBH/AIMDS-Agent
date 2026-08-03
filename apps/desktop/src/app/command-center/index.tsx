import { useStore } from '@nanostores/react'
import {
  IconDownload,
  IconRefresh,
  IconCopy,
  IconPlus,
  IconCheck
} from '@tabler/icons-react'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { SystemStatusContent } from '@/app/settings/gateway-settings'
import { PageLoader } from '@/components/page-loader'
import { ReportIssueDialog } from '@/components/report-issue-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { SearchField } from '@/components/ui/search-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import {
  getActionStatus,
  getLogs,
  getStatus,
  getUsageAnalytics,
  restartGateway,
  updateHermes
} from '@/hermes'
import type { ActionStatusResponse, AnalyticsResponse, StatusResponse } from '@/hermes'
import { useI18n } from '@/i18n'
import { Activity, AlertCircle, BarChart3, Copy, ExternalLink, HelpCircle, Terminal } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { upsertDesktopActionTask } from '@/store/activity'
import { $supportTickets, clearResolvedSupportTickets, isTicketResolved } from '@/store/support-tickets'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { OverlayMain, OverlayNavItem, OverlaySidebar, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'

export type CommandCenterSection = 'system' | 'logs' | 'usage' | 'support'

const SECTIONS = ['system', 'logs', 'usage', 'support'] as const satisfies readonly CommandCenterSection[]

const USAGE_PERIODS = [7, 30, 90] as const
type UsagePeriod = (typeof USAGE_PERIODS)[number]

interface CommandCenterViewProps {
  initialSection?: CommandCenterSection
  onClose: () => void
  onDeleteSession?: (sessionId: string) => Promise<void>
  onNavigateRoute?: (path: string) => void
  onOpenSession?: (sessionId: string) => void
}

interface OutlookDeviceCodePrompt {
  userCode?: string
  verificationUri: string
}

function extractOutlookDeviceCodePrompt(lines: readonly string[]): OutlookDeviceCodePrompt | null {
  if (!lines.length) {
    return null
  }

  const joined = lines.join('\n')
  const inline = joined.match(/open\s+(https?:\/\/\S+)\s+and\s+enter\s+([A-Z0-9-]+)/i)
  if (inline) {
    return {
      verificationUri: inline[1].replace(/[|)\].,;]+$/g, ''),
      userCode: inline[2].trim()
    }
  }

  let verificationUri = ''
  let userCode = ''
  for (const line of lines) {
    if (!verificationUri) {
      const open = line.match(/Open:\s*(https?:\/\/\S+)/i)
      if (open) {
        verificationUri = open[1].replace(/[|)\].,;]+$/g, '')
      }
    }
    if (!userCode) {
      const enter = line.match(/Enter:\s*([A-Z0-9-]+)/i)
      if (enter) {
        userCode = enter[2]?.trim() || enter[1].trim()
      }
    }
  }

  if (verificationUri && userCode) {
    return { verificationUri, userCode }
  }

  const signInIndex = lines.findIndex(line => /sign-in required/i.test(line))
  if (signInIndex >= 0) {
    for (let i = signInIndex; i < lines.length; i += 1) {
      const urlMatch = lines[i].match(/(https?:\/\/\S+)/)
      if (urlMatch) {
        return { verificationUri: urlMatch[1].replace(/[|)\].,;]+$/g, '') }
      }
    }
  }

  return null
}

function EmptyPanel({ action, description, title }: { action?: ReactNode; description: string; title?: string }) {
  return (
    <div className="grid min-h-48 place-items-center px-6 text-center">
      <div>
        {title && (
          <div className="text-[length:var(--conversation-text-font-size)] font-medium text-foreground">{title}</div>
        )}
        <div className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
          {description}
        </div>
        {action && <div className="mt-3 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}

export function CommandCenterView({ initialSection, onClose }: CommandCenterViewProps) {
  const { t } = useI18n()
  const cc = t.commandCenter

  const [section, setSection] = useRouteEnumParam('section', SECTIONS, initialSection ?? 'system')

  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [systemLoading, setSystemLoading] = useState(false)
  const [systemError, setSystemError] = useState('')
  const [outlookPrompt, setOutlookPrompt] = useState<null | OutlookDeviceCodePrompt>(null)
  const [systemAction, setSystemAction] = useState<ActionStatusResponse | null>(null)

  // Logs state
  const [logFile, setLogFile] = useState<'agent' | 'gateway' | 'desktop' | 'error'>('agent')
  const [logLinesCount, setLogLinesCount] = useState<number>(150)
  const [logsFilter, setLogsFilter] = useState('')
  const [logs, setLogs] = useState<string[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsCopied, setLogsCopied] = useState(false)

  // Usage state
  const [usagePeriod, setUsagePeriod] = useState<UsagePeriod>(30)
  const [usage, setUsage] = useState<AnalyticsResponse | null>(null)
  const [usageLoading, setUsageLoading] = useState(false)
  const [usageError, setUsageError] = useState('')
  const usageRequestRef = useRef(0)

  // Support state
  const [reportIssueOpen, setReportIssueOpen] = useState(false)
  const [supportFilter, setSupportFilter] = useState<'ALL' | 'OPEN' | 'IN_PROGRESS' | 'RESOLVED'>('ALL')
  const supportTickets = useStore($supportTickets)

  const refreshSystem = useCallback(async () => {
    setSystemLoading(true)
    setSystemError('')

    try {
      const nextStatus = await getStatus()
      setStatus(nextStatus)
    } catch (error) {
      setSystemError(error instanceof Error ? error.message : String(error))
    } finally {
      setSystemLoading(false)
    }
  }, [])

  const fetchLogsData = useCallback(async () => {
    setLogsLoading(true)
    try {
      const res = await getLogs({ file: logFile, lines: logLinesCount })
      setLogs(res.lines || [])
    } catch (err) {
      setLogs([`Protokollfehler: ${err instanceof Error ? err.message : String(err)}`])
    } finally {
      setLogsLoading(false)
    }
  }, [logFile, logLinesCount])

  const refreshUsage = useCallback(async (days: UsagePeriod) => {
    const requestId = usageRequestRef.current + 1
    usageRequestRef.current = requestId
    setUsageLoading(true)
    setUsageError('')

    try {
      const response = await getUsageAnalytics(days)

      if (usageRequestRef.current === requestId) {
        setUsage(response)
      }
    } catch (error) {
      if (usageRequestRef.current === requestId) {
        setUsageError(error instanceof Error ? error.message : String(error))
      }
    } finally {
      if (usageRequestRef.current === requestId) {
        setUsageLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    if (section === 'system' && !status && !systemLoading) {
      void refreshSystem()
    }
  }, [refreshSystem, section, status, systemLoading])

  useEffect(() => {
    if (section === 'logs') {
      void fetchLogsData()
    }
  }, [fetchLogsData, section])

  useEffect(() => {
    if (section === 'usage') {
      void refreshUsage(usagePeriod)
    }
  }, [refreshUsage, section, usagePeriod])

  useRefreshHotkey(() => {
    if (section === 'system') {
      void refreshSystem()
    } else if (section === 'logs') {
      void fetchLogsData()
    } else if (section === 'usage') {
      void refreshUsage(usagePeriod)
    }
  })

  const runSystemAction = useCallback(
    async (kind: 'restart' | 'update') => {
      setSystemError('')

      try {
        const started = kind === 'restart' ? await restartGateway() : await updateHermes()
        let nextStatus: ActionStatusResponse | null = null
        let promptCaptured = false
        if (kind === 'restart') {
          setOutlookPrompt(null)
        }

        for (let attempt = 0; attempt < 18; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 1200))
          const polled = await getActionStatus(started.name, 180)
          nextStatus = polled
          setSystemAction(polled)
          if (kind === 'restart' && !promptCaptured) {
            const prompt = extractOutlookDeviceCodePrompt(polled.lines)
            if (prompt) {
              promptCaptured = true
              setOutlookPrompt(prompt)
            }
          }
          upsertDesktopActionTask(polled)

          if (!polled.running) {
            break
          }
        }

        if (!nextStatus) {
          const pendingStatus = {
            exit_code: null,
            lines: [cc.actionStartedWaiting],
            name: started.name,
            pid: started.pid,
            running: true
          }

          setSystemAction(pendingStatus)
          upsertDesktopActionTask(pendingStatus)
        }
      } catch (error) {
        setSystemError(error instanceof Error ? error.message : String(error))
      } finally {
        void refreshSystem()
      }
    },
    [cc, refreshSystem]
  )

  const filteredLogs = useMemo(() => {
    if (!logsFilter.trim()) return logs
    const needle = logsFilter.toLowerCase()
    return logs.filter(l => l.toLowerCase().includes(needle))
  }, [logs, logsFilter])

  const handleCopyLogs = () => {
    void navigator.clipboard.writeText(logs.join('\n'))
    setLogsCopied(true)
    setTimeout(() => setLogsCopied(false), 1500)
  }

  const handleExportLogs = () => {
    const content = logs.join('\n')
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `hermes-${logFile}-${new Date().toISOString().slice(0, 10)}.log`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const filteredSupportTickets = useMemo(() => {
    if (supportFilter === 'ALL') return supportTickets
    return supportTickets.filter(t => {
      const status = (t.status || 'OPEN').toUpperCase()
      if (supportFilter === 'RESOLVED') return isTicketResolved(status)
      if (supportFilter === 'IN_PROGRESS') return status === 'IN_PROGRESS' || status === 'PROCESSING'
      if (supportFilter === 'OPEN') return status === 'OPEN' || status === 'QUEUED'
      return true
    })
  }, [supportTickets, supportFilter])

  const getSectionIcon = (val: CommandCenterSection) => {
    switch (val) {
      case 'system':
        return Activity
      case 'logs':
        return Terminal
      case 'usage':
        return BarChart3
      case 'support':
        return HelpCircle
    }
  }

  return (
    <OverlayView closeLabel={cc.close} onClose={onClose}>
      <OverlaySplitLayout>
        <OverlaySidebar>
          {SECTIONS.map(value => (
            <OverlayNavItem
              active={section === value}
              icon={getSectionIcon(value)}
              key={value}
              label={cc.sections[value]}
              onClick={() => setSection(value)}
            />
          ))}
        </OverlaySidebar>

        <OverlayMain>
          <header className="mb-4 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-[length:var(--conversation-text-font-size)] font-semibold text-foreground">
                {cc.sections[section]}
              </h2>
              <p className="mt-0.5 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                {cc.sectionDescriptions[section]}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {section === 'usage' && (
                <SegmentedControl
                  onChange={id => setUsagePeriod(Number(id) as UsagePeriod)}
                  options={USAGE_PERIODS.map(value => ({ id: String(value), label: cc.days(value) }))}
                  value={String(usagePeriod)}
                />
              )}
              {section === 'support' && (
                <Button onClick={() => setReportIssueOpen(true)} size="xs" variant="default">
                  <IconPlus className="mr-1 size-3.5" />
                  Problem melden
                </Button>
              )}
            </div>
          </header>

          {section === 'system' ? (
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <SystemStatusContent />
            </div>
          ) : section === 'logs' ? (
            <div className="flex min-h-0 flex-1 flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-3">
                <div className="flex items-center gap-2">
                  <select
                    className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    onChange={e => setLogFile(e.target.value as any)}
                    value={logFile}
                  >
                    <option value="agent">Agent-Protokoll</option>
                    <option value="gateway">Gateway-Protokoll</option>
                    <option value="desktop">Desktop-Protokoll</option>
                    <option value="error">Fehler-Protokoll</option>
                  </select>

                  <select
                    className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    onChange={e => setLogLinesCount(Number(e.target.value))}
                    value={logLinesCount}
                  >
                    <option value={100}>100 Zeilen</option>
                    <option value={250}>250 Zeilen</option>
                    <option value={500}>500 Zeilen</option>
                  </select>

                  <Button disabled={logsLoading} onClick={() => void fetchLogsData()} size="xs" variant="ghost">
                    <IconRefresh className={cn('size-3.5 mr-1', logsLoading && 'animate-spin')} />
                    Aktualisieren
                  </Button>
                </div>

                <div className="flex items-center gap-2">
                  <SearchField
                    containerClassName="w-48"
                    onChange={next => setLogsFilter(next)}
                    placeholder="Filter..."
                    value={logsFilter}
                  />
                  <Button onClick={handleCopyLogs} size="xs" variant="ghost">
                    {logsCopied ? <IconCheck className="size-3.5 mr-1 text-emerald-500" /> : <IconCopy className="size-3.5 mr-1" />}
                    {logsCopied ? 'Kopiert' : 'Kopieren'}
                  </Button>
                  <Button onClick={handleExportLogs} size="xs" variant="outline">
                    <IconDownload className="size-3.5 mr-1" />
                    Log-Datei
                  </Button>
                </div>
              </div>

              <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap wrap-break-word rounded-xl border border-border/80 bg-black/80 p-4 font-mono text-[0.7rem] leading-relaxed text-zinc-300 select-text cursor-text shadow-inner">
                {filteredLogs.length ? filteredLogs.join('\n') : cc.noLogs}
              </pre>
            </div>
          ) : section === 'usage' ? (
            <UsagePanel
              error={usageError}
              loading={usageLoading}
              onRefresh={() => void refreshUsage(usagePeriod)}
              period={usagePeriod}
              usage={usage}
            />
          ) : (
            <div className="flex min-h-0 flex-1 flex-col gap-4">
              <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-3">
                <SegmentedControl
                  onChange={id => setSupportFilter(id as any)}
                  options={[
                    { id: 'ALL', label: 'Alle Cases' },
                    { id: 'OPEN', label: 'Offen' },
                    { id: 'IN_PROGRESS', label: 'In Bearbeitung' },
                    { id: 'RESOLVED', label: 'Gelöst' }
                  ]}
                  value={supportFilter}
                />

                <Button onClick={() => clearResolvedSupportTickets()} size="xs" variant="ghost">
                  Gelöste bereinigen
                </Button>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto">
                {filteredSupportTickets.length === 0 ? (
                  <EmptyPanel
                    action={
                      <Button onClick={() => setReportIssueOpen(true)} size="xs" variant="default">
                        <IconPlus className="mr-1 size-3.5" />
                        Problem melden
                      </Button>
                    }
                    description="Keine Support-Tickets in dieser Kategorie vorhanden."
                    title="Keine Support-Fälle"
                  />
                ) : (
                  <div className="grid gap-2.5 pr-1">
                    {filteredSupportTickets.map(ticket => {
                      const resolved = isTicketResolved(ticket.status)
                      return (
                        <div
                          className="flex flex-col gap-2 rounded-xl border border-border/60 bg-card/60 p-4 transition-all hover:border-border shadow-2xs"
                          key={ticket.jobId}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-xs font-semibold text-primary">
                                  {ticket.caseId || ticket.referenceId || ticket.jobId}
                                </span>
                                <span
                                  className={cn(
                                    'inline-flex items-center rounded-full px-2 py-0.5 text-[0.65rem] font-medium border',
                                    resolved
                                      ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                                      : 'bg-amber-500/10 text-amber-500 border-amber-500/20'
                                  )}
                                >
                                  {ticket.status || 'OPEN'}
                                </span>
                              </div>
                              <h4 className="mt-1 text-sm font-medium text-foreground">
                                {ticket.summary || 'Support-Ticket'}
                              </h4>
                            </div>
                            <span className="shrink-0 text-[0.65rem] text-muted-foreground">
                              {new Date(ticket.createdAt).toLocaleDateString(undefined, { dateStyle: 'medium' })}
                            </span>
                          </div>

                          <div className="flex items-center justify-between border-t border-border/40 pt-2 mt-1 text-xs text-muted-foreground">
                            <span>Kategorie: {ticket.category || 'other'} · Schweregrad: {ticket.severity || 'medium'}</span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </OverlayMain>
      </OverlaySplitLayout>

      <ReportIssueDialog onOpenChange={setReportIssueOpen} open={reportIssueOpen} />
      <OutlookDeviceCodeDialog prompt={outlookPrompt} onClose={() => setOutlookPrompt(null)} />
    </OverlayView>
  )
}

function OutlookDeviceCodeDialog({
  onClose,
  prompt
}: {
  onClose: () => void
  prompt: null | OutlookDeviceCodePrompt
}) {
  const [copied, setCopied] = useState(false)

  return (
    <Dialog open={Boolean(prompt)} onOpenChange={open => !open && onClose()}>
      <DialogContent showCloseButton>
        <DialogHeader>
          <DialogTitle>Outlook authentication required</DialogTitle>
          <DialogDescription>
            {prompt?.userCode
              ? 'Gateway restart triggered Outlook device login. Open the Microsoft page and enter this code.'
              : 'Gateway restart triggered Outlook sign-in. Open the Microsoft page to sign in — no code needed.'}
          </DialogDescription>
        </DialogHeader>

        {prompt && (
          <div className="space-y-3">
            <Button asChild className="w-full" variant="default">
              <a href={prompt.verificationUri} rel="noreferrer" target="_blank">
                <ExternalLink className="size-4" />
                Open Microsoft Login
              </a>
            </Button>
            {prompt.userCode && (
              <div className="flex items-center gap-2">
                <Input readOnly value={prompt.userCode} className="font-mono text-lg font-bold tracking-widest" />
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => {
                    void navigator.clipboard.writeText(prompt.userCode ?? '')
                    setCopied(true)
                    window.setTimeout(() => setCopied(false), 1500)
                  }}
                >
                  <Copy className="size-4" />
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function formatTokens(value: null | number | undefined): string {
  const num = Number(value || 0)

  if (num >= 1_000_000) {
    return `${(num / 1_000_000).toFixed(1)}M`
  }

  if (num >= 1_000) {
    return `${(num / 1_000).toFixed(1)}K`
  }

  return num.toLocaleString()
}

function formatCost(value: null | number | undefined): string {
  const num = Number(value || 0)

  if (num === 0) {
    return '$0.00'
  }

  if (num < 0.01) {
    return '<$0.01'
  }

  return `$${num.toFixed(2)}`
}

function formatInteger(value: null | number | undefined): string {
  return Number(value ?? 0).toLocaleString()
}

interface UsagePanelProps {
  error: string
  loading: boolean
  onRefresh: () => void
  period: UsagePeriod
  usage: AnalyticsResponse | null
}

function UsagePanel({ error, loading, onRefresh, period, usage }: UsagePanelProps) {
  const { t } = useI18n()
  const cc = t.commandCenter
  const daily = useMemo(() => usage?.daily ?? [], [usage])
  const totals = usage?.totals
  const byModel = usage?.by_model ?? []
  const topSkills = usage?.skills?.top_skills ?? []

  const handleExportCsv = () => {
    if (!usage) return
    const headers = ['Datum', 'Eingabe-Tokens', 'Ausgabe-Tokens', 'Gesamt-Tokens']
    const rows = (usage.daily ?? []).map(d => [
      d.day,
      d.input_tokens || 0,
      d.output_tokens || 0,
      (d.input_tokens || 0) + (d.output_tokens || 0)
    ])
    const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map(r => r.join(','))].join('\n')
    const encodedUri = encodeURI(csvContent)
    const link = document.createElement('a')
    link.setAttribute('href', encodedUri)
    link.setAttribute('download', `hermes-nutzung-${period}tage.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const maxTokens = useMemo(() => {
    if (!daily.length) {
      return 1
    }

    return daily.reduce((acc, entry) => Math.max(acc, (entry.input_tokens || 0) + (entry.output_tokens || 0)), 1)
  }, [daily])

  if (!totals) {
    return (
      <div className="min-h-0 flex-1">
        {loading ? (
          <PageLoader className="min-h-48" label={cc.loadingUsage} />
        ) : (
          <EmptyPanel
            action={
              <Button onClick={onRefresh} size="xs" variant="text">
                {cc.retry}
              </Button>
            }
            description={cc.noUsage(period)}
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto pb-2">
      {error && (
        <span className="inline-flex items-center gap-1 text-[length:var(--conversation-caption-font-size)] text-destructive">
          <AlertCircle className="size-3.5" />
          {error}
        </span>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-4 border-b border-(--ui-stroke-tertiary) pb-5 sm:grid-cols-4">
        <UsageStat label={cc.statSessions} value={formatInteger(totals.total_sessions)} />
        <UsageStat label={cc.statApiCalls} value={formatInteger(totals.total_api_calls)} />
        <UsageStat
          label={cc.statTokens}
          value={`${formatTokens(totals.total_input)} / ${formatTokens(totals.total_output)}`}
        />
        <UsageStat
          hint={totals.total_actual_cost > 0 ? cc.actualCost(formatCost(totals.total_actual_cost)) : undefined}
          label={cc.statCost}
          value={formatCost(totals.total_estimated_cost)}
        />
      </div>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-[0.625rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
            {cc.dailyTokens}
          </span>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-3 text-[0.65rem] text-(--ui-text-tertiary)">
              <span className="inline-flex items-center gap-1">
                <span className="size-2 rounded-[1px] bg-[color:var(--dt-primary)]/60" /> {cc.input}
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="size-2 rounded-[1px] bg-emerald-500/70" /> {cc.output}
              </span>
            </span>
            <Button onClick={handleExportCsv} size="xs" variant="text">
              <IconDownload className="size-3.5 mr-1" />
              CSV
            </Button>
          </div>
        </div>
        {daily.length === 0 ? (
          <div className="grid h-24 place-items-center text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            {cc.noDailyActivity}
          </div>
        ) : (
          <>
            <div className="flex h-24 items-end gap-px">
              {daily.map(entry => {
                const inputH = Math.round(((entry.input_tokens || 0) / maxTokens) * 96)
                const outputH = Math.round(((entry.output_tokens || 0) / maxTokens) * 96)

                return (
                  <div
                    className="group relative flex h-24 min-w-0 flex-1 flex-col justify-end"
                    key={entry.day}
                    title={`${entry.day} · in ${formatTokens(entry.input_tokens)} · out ${formatTokens(entry.output_tokens)}`}
                  >
                    <div
                      className="w-full rounded-t-[1px] bg-[color:var(--dt-primary)]/50"
                      style={{ height: Math.max(inputH, entry.input_tokens > 0 ? 1 : 0) }}
                    />
                    <div
                      className="w-full bg-emerald-500/60"
                      style={{ height: Math.max(outputH, entry.output_tokens > 0 ? 1 : 0) }}
                    />
                  </div>
                )
              })}
            </div>
            <div className="mt-1 flex justify-between text-[0.6rem] text-(--ui-text-tertiary)">
              <span>{daily[0]?.day}</span>
              <span>{daily[daily.length - 1]?.day}</span>
            </div>
          </>
        )}
      </section>

      <div className="grid min-h-0 gap-x-8 gap-y-5 border-t border-(--ui-stroke-tertiary) pt-5 sm:grid-cols-2">
        <UsageList
          emptyLabel={cc.noModelUsage}
          rows={byModel.slice(0, 6).map(entry => ({
            key: entry.model,
            label: entry.model,
            value: `${formatTokens((entry.input_tokens || 0) + (entry.output_tokens || 0))} · ${formatCost(entry.estimated_cost)}`
          }))}
          title={cc.topModels}
        />
        <UsageList
          emptyLabel={cc.noSkillActivity}
          rows={topSkills.slice(0, 6).map(entry => ({
            key: entry.skill,
            label: entry.skill,
            value: cc.actions(entry.total_count.toLocaleString())
          }))}
          title={cc.topSkills}
        />
      </div>
    </div>
  )
}

function UsageList({
  emptyLabel,
  rows,
  title
}: {
  emptyLabel: string
  rows: Array<{ key: string; label: string; value: string }>
  title: string
}) {
  return (
    <section className="min-w-0">
      <div className="mb-1.5 text-[0.625rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
        {title}
      </div>
      {rows.length === 0 ? (
        <div className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          {emptyLabel}
        </div>
      ) : (
        <ul>
          {rows.map(row => (
            <li className="flex items-center justify-between gap-2 py-1.5" key={row.key}>
              <span className="min-w-0 truncate font-mono text-[0.7rem] text-foreground">{row.label}</span>
              <span className="shrink-0 text-[0.65rem] text-(--ui-text-tertiary)">{row.value}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function UsageStat({ hint, label, value }: { hint?: string; label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[0.625rem] font-medium uppercase tracking-[0.12em] text-(--ui-text-tertiary)">{label}</div>
      <div className="mt-1 truncate text-base font-semibold tracking-tight text-foreground">{value}</div>
      {hint && <div className="mt-0.5 truncate text-[0.62rem] text-(--ui-text-tertiary)">{hint}</div>}
    </div>
  )
}
