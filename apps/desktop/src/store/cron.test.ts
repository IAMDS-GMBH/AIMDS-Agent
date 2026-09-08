import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CronJob } from '@/types/hermes'

import { $cronJobs, $cronJobsWithNewOutput, $unseenCronOutputCount, markCronJobSeen, setCronJobs } from './cron'

function job(overrides: Partial<CronJob> = {}): CronJob {
  return { enabled: true, id: 'job-1', ...overrides }
}

describe('cron store', () => {
  let api: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn()
    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { api } })
    setCronJobs([])
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
    setCronJobs([])
  })

  it('derives the unseen jobs sorted by newest output', () => {
    setCronJobs([
      job({ id: 'old', last_output_at: '2026-09-07T06:00:00Z' }),
      job({ id: 'seen', last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T06:30:00Z' }),
      job({ id: 'new', last_output_at: '2026-09-08T07:00:00Z' }),
      job({ id: 'failed', last_output_at: '2026-09-08T08:00:00Z', last_status: 'error' }),
      job({ id: 'none' })
    ])

    expect($cronJobsWithNewOutput.get().map(row => row.id)).toEqual(['new', 'old'])
    expect($unseenCronOutputCount.get()).toBe(2)
  })

  it('marks seen optimistically and merges the server row', async () => {
    setCronJobs([job({ id: 'j', last_output_at: '2026-09-08T06:00:00Z', profile: 'work' })])
    api.mockResolvedValueOnce({ id: 'j', last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T09:00:00Z' })

    const pending = markCronJobSeen('j')

    expect($unseenCronOutputCount.get()).toBe(0)
    await pending

    expect(api).toHaveBeenCalledWith({ method: 'POST', path: '/api/cron/jobs/j/seen?profile=work' })
    expect($cronJobs.get()[0].last_seen_at).toBe('2026-09-08T09:00:00Z')
    expect($unseenCronOutputCount.get()).toBe(0)
  })

  it('refetches the list when the seen call fails', async () => {
    setCronJobs([job({ id: 'j', last_output_at: '2026-09-08T06:00:00Z' })])
    api.mockRejectedValueOnce(new Error('offline'))
    api.mockResolvedValueOnce([job({ id: 'j', last_output_at: '2026-09-08T06:00:00Z' })])

    await markCronJobSeen('j')

    expect(api).toHaveBeenLastCalledWith({ path: '/api/cron/jobs' })
    // Server still says unseen → the optimistic flip is rolled back.
    expect($unseenCronOutputCount.get()).toBe(1)
  })

  it('is a no-op for unknown or already-seen jobs', async () => {
    setCronJobs([job({ id: 'j', last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T07:00:00Z' })])

    await markCronJobSeen('j')
    await markCronJobSeen('missing')

    expect(api).not.toHaveBeenCalled()
  })
})
