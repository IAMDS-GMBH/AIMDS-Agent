/**
 * The left navigation's model (AIS-794), kept pure so it is testable: which
 * entries the sidebar shows, how sessions group by time, and what the
 * "Im Hintergrund" section lists.
 *
 * Layout, top to bottom: "Neue Sitzung" (primary) and the search, the nav
 * entries below, "Im Hintergrund" (agent runs + automations), "Favoriten"
 * (pins), the sessions by time (Heute / Gestern / Diese Woche / Älter), and
 * the account menu at the foot.
 */

import type { SessionInfo } from '@/hermes'
import type { DesktopActionTask } from '@/store/activity'
import type { SubagentProgress, SubagentStatus } from '@/store/subagents'
import type { CronJob } from '@/types/hermes'

import { compareByOutputDesc, hasNewOutput, jobState, jobTitle } from '../../cron/job-state'
import { HUB_ROUTE, TODOS_ROUTE } from '../../routes'
import type { SidebarNavId } from '../../types'

import { sessionTime } from './session-order'

// ─── Nav entries ────────────────────────────────────────────────────────────

export interface SidebarNavSlot {
  id: SidebarNavId
  /** Codicon name. */
  icon: string
  /** Where the entry leads. A slot without a route is reserved: not shown. */
  route?: string
  /** Shown only in the developer view (Settings → Appearance). */
  developerOnly?: boolean
}

/**
 * Every nav entry in display order. "Start" and "Aufgaben" are reserved for
 * the tickets that build those pages: give the slot a route and it appears —
 * no layout change needed. Skills & Tools moved to Settings → Fähigkeiten,
 * Artefakte to the command palette.
 */
export const SIDEBAR_NAV_SLOTS: readonly SidebarNavSlot[] = [
  { id: 'start', icon: 'home' },
  { id: 'tasks', icon: 'tasklist' },
  { id: 'todos', icon: 'checklist', route: TODOS_ROUTE },
  { id: 'discover', icon: 'compass', route: HUB_ROUTE, developerOnly: true }
]

export interface SidebarNavContext {
  developer: boolean
}

export function visibleNavSlots(
  { developer }: SidebarNavContext,
  slots: readonly SidebarNavSlot[] = SIDEBAR_NAV_SLOTS
): SidebarNavSlot[] {
  return slots.filter(slot => Boolean(slot.route) && (developer || !slot.developerOnly))
}

// ─── Sessions by time ───────────────────────────────────────────────────────

export type SessionTimeGroupId = 'today' | 'yesterday' | 'week' | 'older'

export interface SessionTimeGroup {
  id: SessionTimeGroupId
  sessions: SessionInfo[]
}

const DAY_MS = 86_400_000

const startOfDay = (ms: number) => {
  const date = new Date(ms)
  date.setHours(0, 0, 0, 0)

  return date.getTime()
}

// Calendar week, Monday first (as in DACH calendars).
const startOfWeek = (ms: number) => {
  const day = startOfDay(ms)
  const weekday = (new Date(day).getDay() + 6) % 7

  return new Date(day).setDate(new Date(day).getDate() - weekday)
}

export function sessionTimeGroup(session: SessionInfo, nowMs: number): SessionTimeGroupId {
  const ms = sessionTime(session) * 1000
  const today = startOfDay(nowMs)

  if (ms >= today) {
    return 'today'
  }

  if (ms >= startOfDay(today - DAY_MS / 2)) {
    return 'yesterday'
  }

  return ms >= startOfWeek(nowMs) ? 'week' : 'older'
}

/**
 * Heute / Gestern / Diese Woche / Älter, in local time. "Diese Woche" is the
 * current calendar week before yesterday. Rows keep their order (the caller
 * sorts by latest activity); empty groups are left out.
 */
export function groupSessionsByTime(sessions: readonly SessionInfo[], nowMs: number): SessionTimeGroup[] {
  const groups: Record<SessionTimeGroupId, SessionInfo[]> = { today: [], yesterday: [], week: [], older: [] }

  for (const session of sessions) {
    groups[sessionTimeGroup(session, nowMs)].push(session)
  }

  return (['today', 'yesterday', 'week', 'older'] as const)
    .map(id => ({ id, sessions: groups[id] }))
    .filter(group => group.sessions.length > 0)
}

// ─── "Im Hintergrund" ───────────────────────────────────────────────────────

export type BackgroundRunStatus = 'done' | 'failed' | 'running'

export type BackgroundItem =
  | { kind: 'agent'; id: string; label: string; status: BackgroundRunStatus; updatedAt: number }
  | { kind: 'action'; id: string; label: string; status: BackgroundRunStatus; updatedAt: number }
  | { kind: 'cron'; id: string; job: CronJob }

export interface BackgroundSummary {
  items: BackgroundItem[]
  /** Agent runs, actions and automations working right now. */
  running: number
  /** Automations with output the user has not opened yet (the "new" badge). */
  unseen: number
}

/** Finished runs worth a glance; older ones live on the agents page. */
export const BACKGROUND_FINISHED_RUN_LIMIT = 3

const AGENT_STATUS: Record<SubagentStatus, BackgroundRunStatus> = {
  completed: 'done',
  failed: 'failed',
  interrupted: 'failed',
  queued: 'running',
  running: 'running'
}

function nextRunMs(job: CronJob): null | number {
  const ms = job.next_run_at ? Date.parse(job.next_run_at) : Number.NaN

  return Number.isNaN(ms) ? null : ms
}

/**
 * Automations in sidebar order: unseen output first (newest first), then the
 * soonest next run, jobs without a next run last, then by name.
 */
export function sortCronJobs(jobs: readonly CronJob[]): CronJob[] {
  return [...jobs].sort((a, b) => {
    const aNew = hasNewOutput(a)
    const bNew = hasNewOutput(b)

    if (aNew !== bNew) {
      return aNew ? -1 : 1
    }

    if (aNew && bNew) {
      return compareByOutputDesc(a, b)
    }

    const an = nextRunMs(a)
    const bn = nextRunMs(b)

    if (an !== null && bn !== null && an !== bn) {
      return an - bn
    }

    if (an === null && bn !== null) {
      return 1
    }

    if (an !== null && bn === null) {
      return -1
    }

    return jobTitle(a).localeCompare(jobTitle(b))
  })
}

/**
 * One list for everything that runs without the user watching: agent runs
 * (subagents of every session) and desktop actions, then the automations.
 * Running work leads, then automations with new output, then the latest
 * finished runs, then the remaining automations.
 */
export function backgroundSummary(
  cronJobs: readonly CronJob[],
  subagentsBySession: Record<string, readonly SubagentProgress[]>,
  actionTasks: Record<string, DesktopActionTask>
): BackgroundSummary {
  const runs: Extract<BackgroundItem, { kind: 'action' | 'agent' }>[] = []

  for (const items of Object.values(subagentsBySession)) {
    for (const item of items) {
      // Top-level runs only: a run's own helpers show on the agents page.
      if (!item.parentId) {
        runs.push({
          id: item.id,
          kind: 'agent',
          label: item.goal,
          status: AGENT_STATUS[item.status],
          updatedAt: item.updatedAt
        })
      }
    }
  }

  for (const { status, updatedAt } of Object.values(actionTasks)) {
    runs.push({
      id: status.name,
      kind: 'action',
      label: status.name,
      status: status.running ? 'running' : (status.exit_code ?? 0) === 0 ? 'done' : 'failed',
      updatedAt
    })
  }

  const newestFirst = (a: { updatedAt: number }, b: { updatedAt: number }) => b.updatedAt - a.updatedAt
  const running = runs.filter(run => run.status === 'running').sort(newestFirst)
  const finished = runs.filter(run => run.status !== 'running').sort(newestFirst)
  const jobs = sortCronJobs(cronJobs).map(job => ({ id: job.id, job, kind: 'cron' as const }))
  const unseenJobs = jobs.filter(item => hasNewOutput(item.job))
  const otherJobs = jobs.filter(item => !hasNewOutput(item.job))

  return {
    items: [...running, ...unseenJobs, ...finished.slice(0, BACKGROUND_FINISHED_RUN_LIMIT), ...otherJobs],
    running: running.length + cronJobs.filter(job => jobState(job) === 'running').length,
    unseen: unseenJobs.length
  }
}
