import { describe, expect, it } from 'vitest'

import type { CronJob } from '@/types/hermes'

import { compareByOutputDesc, hasNewOutput, isBriefJob, outputDate } from './job-state'

function job(overrides: Partial<CronJob> = {}): CronJob {
  return { enabled: true, id: 'job-1', ...overrides }
}

describe('hasNewOutput', () => {
  it('is false without an output timestamp', () => {
    expect(hasNewOutput(job())).toBe(false)
    expect(hasNewOutput(job({ last_output_at: null, last_output_path: '/tmp/a.md' }))).toBe(false)
    expect(hasNewOutput(job({ last_output_at: 'not-a-date' }))).toBe(false)
  })

  it('is true when the output was never seen', () => {
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z' }))).toBe(true)
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_seen_at: null }))).toBe(true)
  })

  it('compares seen and output as instants', () => {
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T05:59:00Z' }))).toBe(
      true
    )
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T06:00:00Z' }))).toBe(
      false
    )
    expect(
      hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T08:00:00+02:00' }))
    ).toBe(false)
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T07:00:00Z' }))).toBe(
      false
    )
  })

  it('never counts a failed run as new output', () => {
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_status: 'error' }))).toBe(false)
    expect(hasNewOutput(job({ last_output_at: '2026-09-08T06:00:00Z', last_status: 'ok' }))).toBe(true)
  })
})

describe('outputDate', () => {
  it('parses ISO timestamps and rejects garbage', () => {
    expect(outputDate(job({ last_output_at: '2026-09-08T06:00:00Z' }))?.toISOString()).toBe('2026-09-08T06:00:00.000Z')
    expect(outputDate(job({ last_output_at: 'nope' }))).toBeNull()
    expect(outputDate(job())).toBeNull()
  })
})

describe('isBriefJob', () => {
  it('recognises the seeded brief jobs by origin', () => {
    expect(isBriefJob(job({ origin: { seed_key: 'morning-brief' } }))).toBe(true)
    expect(isBriefJob(job({ origin: { seed_key: 'Weekly-Review' } }))).toBe(true)
    expect(isBriefJob(job({ origin: { seed_key: 'inbox-sweep' } }))).toBe(false)
  })

  it('falls back to name / prompt heuristics', () => {
    expect(isBriefJob(job({ name: 'Morning Brief' }))).toBe(true)
    expect(isBriefJob(job({ name: 'Morgenbriefing' }))).toBe(true)
    expect(isBriefJob(job({ prompt: 'Write the weekly review for the team' }))).toBe(true)
    expect(isBriefJob(job({ name: 'Backup vault', prompt: 'rsync the vault' }))).toBe(false)
  })
})

describe('compareByOutputDesc', () => {
  it('sorts newest output first and jobs without output last', () => {
    const a = job({ id: 'a', last_output_at: '2026-09-08T06:00:00Z' })
    const b = job({ id: 'b', last_output_at: '2026-09-08T07:00:00Z' })
    const c = job({ id: 'c' })

    expect([c, a, b].sort(compareByOutputDesc).map(row => row.id)).toEqual(['b', 'a', 'c'])
  })
})
