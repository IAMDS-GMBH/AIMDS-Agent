import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/hermes'

import { sortByLatestActivity, workspaceGroupsFor } from './session-order'

const session = (id: string, started_at: number, last_active: number, cwd: null | string = null) =>
  ({ id, started_at, last_active, cwd }) as unknown as SessionInfo

describe('sidebar session order (AIS-459)', () => {
  it('moves a session to the top when it gets a new message, without a new id', () => {
    const before = [session('a', 100, 300), session('b', 200, 250), session('c', 50, 120)]

    expect(sortByLatestActivity(before).map(s => s.id)).toEqual(['a', 'b', 'c'])

    // An old chat ('c') is continued: same id, newer last_active.
    const after = before.map(s => (s.id === 'c' ? { ...s, last_active: 400 } : s))

    expect(sortByLatestActivity(after).map(s => s.id)).toEqual(['c', 'a', 'b'])
  })

  it('falls back to the start time for sessions without activity', () => {
    const sessions = [session('old', 100, 0), session('new', 500, 0), session('active', 10, 300)]

    expect(sortByLatestActivity(sessions).map(s => s.id)).toEqual(['new', 'active', 'old'])
  })

  it('orders workspace groups and their rows by the latest activity', () => {
    const sorted = sortByLatestActivity([
      session('w1-old-active', 10, 900, '/repo/one'),
      session('w2', 300, 500, '/repo/two'),
      session('w1-new-idle', 400, 400, '/repo/one')
    ])

    const groups = workspaceGroupsFor(sorted, 'No workspace')

    expect(groups.map(g => g.label)).toEqual(['one', 'two'])
    expect(groups[0].sessions.map(s => s.id)).toEqual(['w1-old-active', 'w1-new-idle'])
  })
})
