import { describe, expect, it } from 'vitest'

import type { CronJob } from '@/types/hermes'

import { jobState } from './job-state'

// AIS-599: a run skipped for a pending re-login is neither an error nor a pause.
describe('jobState', () => {
  it('shows waiting for a scheduled job skipped for a re-login', () => {
    expect(jobState({ id: 'a', last_status: 'waiting_reauth', state: 'scheduled' } as CronJob)).toBe('waiting')
    expect(jobState({ id: 'b', last_status: 'waiting_reauth' } as CronJob)).toBe('waiting')
  })

  it('keeps explicit states', () => {
    expect(jobState({ id: 'c', last_status: 'waiting_reauth', state: 'paused' } as CronJob)).toBe('paused')
    expect(jobState({ enabled: false, id: 'd' } as CronJob)).toBe('disabled')
    expect(jobState({ id: 'e', last_status: 'ok' } as CronJob)).toBe('scheduled')
  })
})
