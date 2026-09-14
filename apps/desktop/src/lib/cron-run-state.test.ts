import { describe, expect, it } from 'vitest'

import type { CronJob } from '@/types/hermes'

import type { ChatMessage } from './chat-messages'
import { cronRunOutcome, failedCronRunJob, isCronRunFinished } from './cron-run-state'

const SESSION = 'cron_aimds-morning-brief_20260914_080832'

const user: ChatMessage = { id: 'u1', role: 'user', parts: [] }
const reply: ChatMessage = { id: 'a1', role: 'assistant', parts: [] }
const pendingReply: ChatMessage = { id: 'a2', role: 'assistant', parts: [], pending: true }

function job(last_status: CronJob['last_status'], extra: Partial<CronJob> = {}): CronJob {
  return { id: 'aimds-morning-brief', enabled: true, last_run_session_id: SESSION, last_status, ...extra }
}

describe('cronRunOutcome (AIS-332)', () => {
  it('reports the job status when the job record knows the run', () => {
    expect(cronRunOutcome(SESSION, [user], [job('ok')])).toBe('ok')
    expect(cronRunOutcome(SESSION, [user], [job('error')])).toBe('error')
  })

  it('falls back to the transcript when the job carries no status', () => {
    expect(cronRunOutcome(SESSION, [user], [job(null)])).toBe('running')
    expect(cronRunOutcome(SESSION, [user, pendingReply], [job(null)])).toBe('running')
    expect(cronRunOutcome(SESSION, [user, reply], [job(null)])).toBe('ok')
    expect(cronRunOutcome(SESSION, [user, reply], null)).toBe('ok')
  })

  it('isCronRunFinished treats ok and error alike', () => {
    expect(isCronRunFinished(SESSION, [user], [job('error')])).toBe(true)
    expect(isCronRunFinished(SESSION, [user], [job('ok')])).toBe(true)
    expect(isCronRunFinished(SESSION, [user], [job(null)])).toBe(false)
  })
})

describe('failedCronRunJob (SUP-20260914-063903)', () => {
  it('returns the job for a failed run whose transcript holds only the prompt', () => {
    const failed = job('error', { last_error: 'Provider unreachable (network/DNS)' })
    expect(failedCronRunJob(SESSION, [user], [failed])).toBe(failed)
  })

  it('is null for ok runs, running runs, replies present, or non-cron sessions', () => {
    expect(failedCronRunJob(SESSION, [user], [job('ok')])).toBeNull()
    expect(failedCronRunJob(SESSION, [user], [job(null)])).toBeNull()
    expect(failedCronRunJob(SESSION, [user, reply], [job('error')])).toBeNull()
    expect(failedCronRunJob('20260914_111919_e7c4cc', [user], [job('error')])).toBeNull()
    expect(failedCronRunJob(null, [user], [job('error')])).toBeNull()
  })
})
