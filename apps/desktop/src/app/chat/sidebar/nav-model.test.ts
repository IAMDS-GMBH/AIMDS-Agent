import { describe, expect, it } from 'vitest'

import { HUB_ROUTE, TODOS_ROUTE } from '@/app/routes'
import type { SessionInfo } from '@/hermes'
import type { DesktopActionTask } from '@/store/activity'
import type { SubagentProgress } from '@/store/subagents'
import type { CronJob } from '@/types/hermes'

import {
  BACKGROUND_FINISHED_RUN_LIMIT,
  backgroundSummary,
  groupSessionsByTime,
  SIDEBAR_NAV_SLOTS,
  sortCronJobs,
  visibleNavSlots
} from './nav-model'

describe('sidebar nav entries (AIS-794)', () => {
  it('shows Todos to everyone and keeps Discover for the developer view', () => {
    expect(visibleNavSlots({ developer: false }).map(slot => slot.id)).toEqual(['todos'])
    expect(visibleNavSlots({ developer: true }).map(slot => [slot.id, slot.route])).toEqual([
      ['todos', TODOS_ROUTE],
      ['discover', HUB_ROUTE]
    ])
  })

  it('reserves Start and Aufgaben ahead of the other entries without showing them empty', () => {
    expect(SIDEBAR_NAV_SLOTS.slice(0, 2).map(slot => slot.id)).toEqual(['start', 'tasks'])

    const ready = SIDEBAR_NAV_SLOTS.map(slot => (slot.id === 'start' ? { ...slot, route: '/start' } : slot))

    expect(visibleNavSlots({ developer: false }, ready).map(slot => slot.id)).toEqual(['start', 'todos'])
  })

  it('no longer lists Skills & Tools or Artefakte', () => {
    const ids = SIDEBAR_NAV_SLOTS.map(slot => slot.id as string)

    expect(ids).not.toContain('skills')
    expect(ids).not.toContain('artifacts')
  })
})

// Local time: Friday 2026-10-30, 10:30.
const NOW = new Date(2026, 9, 30, 10, 30).getTime()
const at = (year: number, month: number, day: number, hour = 12) => new Date(year, month - 1, day, hour).getTime() / 1000

const session = (id: string, lastActive: number) =>
  ({ id, last_active: lastActive, started_at: lastActive }) as unknown as SessionInfo

describe('sessions by time (AIS-794)', () => {
  it('groups into Heute / Gestern / Diese Woche / Älter in that order', () => {
    const groups = groupSessionsByTime(
      [
        session('today-early', at(2026, 10, 30, 0)),
        session('yesterday-late', at(2026, 10, 29, 23)),
        session('monday', at(2026, 10, 26, 8)),
        session('last-sunday', at(2026, 10, 25, 20)),
        session('last-year', at(2025, 12, 1))
      ],
      NOW
    )

    expect(groups.map(group => [group.id, group.sessions.map(s => s.id)])).toEqual([
      ['today', ['today-early']],
      ['yesterday', ['yesterday-late']],
      ['week', ['monday']],
      ['older', ['last-sunday', 'last-year']]
    ])
  })

  it('leaves empty groups out and keeps the given row order', () => {
    const groups = groupSessionsByTime(
      [session('b', at(2026, 10, 30, 9)), session('a', at(2026, 10, 30, 8)), session('old', at(2026, 1, 2))],
      NOW
    )

    expect(groups.map(group => group.id)).toEqual(['today', 'older'])
    expect(groups[0].sessions.map(s => s.id)).toEqual(['b', 'a'])
  })

  it('puts yesterday on a Monday into Gestern, not Diese Woche', () => {
    const monday = new Date(2026, 10, 2, 9).getTime()
    const groups = groupSessionsByTime([session('sunday', at(2026, 11, 1, 18))], monday)

    expect(groups.map(group => group.id)).toEqual(['yesterday'])
  })

  it('falls back to the start time for sessions without activity', () => {
    const fresh = { id: 'fresh', last_active: 0, started_at: at(2026, 10, 30, 10) } as unknown as SessionInfo

    expect(groupSessionsByTime([fresh], NOW).map(group => group.id)).toEqual(['today'])
  })
})

const job = (overrides: Partial<CronJob>): CronJob => ({ enabled: true, id: 'job', ...overrides })

const agent = (overrides: Partial<SubagentProgress>): SubagentProgress => ({
  filesRead: [],
  filesWritten: [],
  goal: 'Research',
  id: 'agent',
  parentId: null,
  startedAt: 0,
  status: 'running',
  stream: [],
  taskCount: 1,
  taskIndex: 0,
  updatedAt: 0,
  ...overrides
})

const action = (name: string, running: boolean, exitCode: null | number, updatedAt: number): DesktopActionTask => ({
  status: { exit_code: exitCode, name, running } as DesktopActionTask['status'],
  updatedAt
})

describe('"Im Hintergrund" (AIS-794)', () => {
  it('merges agent runs, actions and automations: running first, then new output, finished runs, the rest', () => {
    const summary = backgroundSummary(
      [
        job({ id: 'later', name: 'Later', next_run_at: '2026-10-31T09:00:00Z' }),
        job({ id: 'fresh', last_output_at: '2026-10-30T07:00:00Z', name: 'Fresh' })
      ],
      {
        'session-a': [
          agent({ id: 'running', status: 'running', updatedAt: 50 }),
          agent({ id: 'helper', parentId: 'running', status: 'running', updatedAt: 60 }),
          agent({ id: 'finished', status: 'completed', updatedAt: 40 })
        ],
        'session-b': [agent({ id: 'failed', status: 'interrupted', updatedAt: 45 })]
      },
      { build: action('build', true, null, 70) }
    )

    expect(summary.items.map(item => `${item.kind}:${item.id}`)).toEqual([
      'action:build',
      'agent:running',
      'cron:fresh',
      'agent:failed',
      'agent:finished',
      'cron:later'
    ])
    expect(summary.running).toBe(2)
    expect(summary.unseen).toBe(1)
  })

  it('keeps the unseen badge logic: seen output and failed runs are not new', () => {
    const summary = backgroundSummary(
      [
        job({ id: 'seen', last_output_at: '2026-10-30T07:00:00Z', last_seen_at: '2026-10-30T08:00:00Z' }),
        job({ id: 'error', last_output_at: '2026-10-30T07:00:00Z', last_status: 'error' }),
        job({ id: 'new', last_output_at: '2026-10-30T09:00:00Z', last_seen_at: '2026-10-30T08:00:00Z' })
      ],
      {},
      {}
    )

    expect(summary.unseen).toBe(1)
    expect(summary.items[0]).toMatchObject({ id: 'new', kind: 'cron' })
  })

  it('shows only the latest finished runs', () => {
    const finished = Array.from({ length: BACKGROUND_FINISHED_RUN_LIMIT + 2 }, (_, i) =>
      agent({ id: `done-${i}`, status: 'completed', updatedAt: i })
    )

    const summary = backgroundSummary([], { s: finished }, {})

    expect(summary.items).toHaveLength(BACKGROUND_FINISHED_RUN_LIMIT)
    expect(summary.items[0]).toMatchObject({ id: `done-${BACKGROUND_FINISHED_RUN_LIMIT + 1}`, status: 'done' })
    expect(summary.running).toBe(0)
  })

  it('counts a running automation and a failed action', () => {
    const summary = backgroundSummary([job({ id: 'busy', state: 'running' })], {}, { sync: action('sync', false, 2, 5) })

    expect(summary.running).toBe(1)
    expect(summary.items.find(item => item.kind === 'action')).toMatchObject({ status: 'failed' })
  })

  it('sorts automations by unseen output, then the next run, then name', () => {
    const sorted = sortCronJobs([
      job({ id: 'none-b', name: 'B' }),
      job({ id: 'soon', next_run_at: '2026-10-30T11:00:00Z' }),
      job({ id: 'none-a', name: 'A' }),
      job({ id: 'new', last_output_at: '2026-10-30T07:00:00Z' }),
      job({ id: 'later', next_run_at: '2026-10-31T11:00:00Z' })
    ])

    expect(sorted.map(item => item.id)).toEqual(['new', 'soon', 'later', 'none-a', 'none-b'])
  })
})
