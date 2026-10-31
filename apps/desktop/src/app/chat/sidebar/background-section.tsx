import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { SidebarGroup, SidebarGroupContent } from '@/components/ui/sidebar'
import { Tip } from '@/components/ui/tooltip'
import type { SessionInfo } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { SidebarPanelLabel } from '../../shell/sidebar-label'

import { CronJobSidebarRow } from './cron-jobs-section'
import { SidebarLoadMoreRow } from './load-more-row'
import type { BackgroundItem, BackgroundRunStatus, BackgroundSummary } from './nav-model'

// Keep the section compact: a few rows up front, more in steps on demand.
const INITIAL_VISIBLE_ITEMS = 4
const LOAD_MORE_STEP = 10

const RUN_DOT: Record<BackgroundRunStatus, string> = {
  done: 'bg-emerald-500',
  failed: 'bg-rose-500',
  running: 'size-1.5 animate-pulse bg-(--theme-primary)'
}

interface SidebarBackgroundSectionProps {
  summary: BackgroundSummary
  open: boolean
  onToggle: () => void
  /** Agent runs and actions: the page with every background run. */
  onOpenRuns: () => void
  /** Automations: the full schedule page (AIS-550 builds the new one). */
  onOpenAutomations: () => void
  onManageJob: (jobId: string) => void
  onOpenArtifact: (jobId: string, run?: SessionInfo) => void
  onOpenRun: (sessionId: string) => void
  onTriggerJob: (jobId: string) => void
}

/**
 * "Im Hintergrund" (AIS-794): one prominent place for everything that runs
 * without the user watching — agent runs, desktop actions and automations
 * (cron jobs, formerly "Automatische Aufgaben" at the bottom). The header
 * counts what is working right now and keeps the "new" badge for automation
 * output the user has not opened yet.
 */
export function SidebarBackgroundSection({
  summary,
  open,
  onToggle,
  onOpenRuns,
  onOpenAutomations,
  onManageJob,
  onOpenArtifact,
  onOpenRun,
  onTriggerJob
}: SidebarBackgroundSectionProps) {
  const { t } = useI18n()
  const s = t.sidebar
  const [nowMs, setNowMs] = useState(() => Date.now())
  // Single-open inline peek so the section stays scannable.
  const [peekJobId, setPeekJobId] = useState<null | string>(null)
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_ITEMS)

  // One clock for the whole section (rows are pure) so the countdowns tick
  // without re-rendering the rest of the sidebar. Only runs while expanded.
  useEffect(() => {
    if (!open) {
      return
    }

    const id = window.setInterval(() => setNowMs(Date.now()), 1000)

    return () => window.clearInterval(id)
  }, [open])

  const shown = summary.items.slice(0, visibleCount)
  const hiddenCount = summary.items.length - shown.length

  return (
    <SidebarGroup className="shrink-0 p-0 pb-1" data-testid="sidebar-background">
      <div className="group/section flex shrink-0 items-center justify-between pb-1 pt-1.5">
        <button
          className="group/section-label flex w-fit items-center gap-1.5 bg-transparent text-left leading-none"
          onClick={onToggle}
          type="button"
        >
          <SidebarPanelLabel className="shrink-0">{s.background}</SidebarPanelLabel>
          {summary.running > 0 && (
            <span className="whitespace-nowrap text-[0.6875rem] font-medium text-(--ui-text-tertiary)">
              {s.backgroundActive(summary.running)}
            </span>
          )}
          {summary.unseen > 0 && (
            <span className="whitespace-nowrap text-[0.6875rem] font-semibold text-(--theme-primary)">
              {t.cron.unseenCount(summary.unseen)}
            </span>
          )}
          <DisclosureCaret
            className="text-(--ui-text-tertiary) opacity-0 transition group-hover/section-label:opacity-100"
            open={open}
          />
        </button>
        <Tip label={s.openAutomations}>
          <button
            aria-label={s.openAutomations}
            className="grid size-5 place-items-center rounded-control-sm text-(--ui-text-tertiary) opacity-0 transition-opacity hover:bg-(--ui-control-hover-background) hover:text-foreground focus-visible:opacity-100 group-hover/section:opacity-100"
            onClick={onOpenAutomations}
            type="button"
          >
            <Codicon name="watch" size="0.75rem" />
          </button>
        </Tip>
      </div>
      {open && (
        <SidebarGroupContent className="flex max-h-72 flex-col gap-px overflow-y-auto overscroll-contain pb-1.75 compact:max-h-none compact:overflow-visible">
          {shown.map(item =>
            item.kind === 'cron' ? (
              <CronJobSidebarRow
                expanded={peekJobId === item.job.id}
                job={item.job}
                key={`cron:${item.id}`}
                nowMs={nowMs}
                onManage={() => onManageJob(item.job.id)}
                onOpenArtifact={run => onOpenArtifact(item.job.id, run)}
                onOpenRun={onOpenRun}
                onTogglePeek={() => setPeekJobId(prev => (prev === item.job.id ? null : item.job.id))}
                onTrigger={() => onTriggerJob(item.job.id)}
              />
            ) : (
              <BackgroundRunRow item={item} key={`${item.kind}:${item.id}`} onOpen={onOpenRuns} />
            )
          )}
          {hiddenCount > 0 && (
            <SidebarLoadMoreRow
              onClick={() => setVisibleCount(count => count + LOAD_MORE_STEP)}
              step={Math.min(LOAD_MORE_STEP, hiddenCount)}
            />
          )}
        </SidebarGroupContent>
      )}
    </SidebarGroup>
  )
}

function BackgroundRunRow({
  item,
  onOpen
}: {
  item: Extract<BackgroundItem, { kind: 'action' | 'agent' }>
  onOpen: () => void
}) {
  const { t } = useI18n()
  const s = t.sidebar

  return (
    <button
      aria-label={`${item.label} · ${s.backgroundRun[item.status]}`}
      className="group/run grid min-h-[1.625rem] grid-cols-[minmax(0,1fr)_auto] items-center gap-1.5 rounded-control-sm bg-transparent py-0.5 pl-2 pr-1 text-left hover:bg-(--chrome-action-hover) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      onClick={onOpen}
      title={s.openBackgroundAgents}
      type="button"
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="grid w-3.5 shrink-0 place-items-center">
          <span aria-hidden="true" className={cn('size-1 rounded-full', RUN_DOT[item.status])} />
        </span>
        <span
          className={cn(
            'min-w-0 truncate text-[0.8125rem] text-(--ui-text-secondary) group-hover/run:text-foreground',
            item.status === 'running' && 'text-foreground'
          )}
        >
          {item.label}
        </span>
      </span>
      <span className="text-[0.6875rem] text-(--ui-text-tertiary)">{s.backgroundRun[item.status]}</span>
    </button>
  )
}
