import { describe, expect, it } from 'vitest'

import type { CronJob } from '@/types/hermes'

import type { ChatMessage } from './chat-messages'
import { isCronRunFinished, isCronSessionId } from './cron-run-state'

const SESSION = 'cron_aimds-morning-brief_20260911_080024'

function message(role: ChatMessage['role'], overrides: Partial<ChatMessage> = {}): ChatMessage {
  return { id: `${role}-${Math.random()}`, parts: [{ type: 'text', text: role }], role, ...overrides }
}

function job(overrides: Partial<CronJob> = {}): CronJob {
  return { id: 'aimds-morning-brief', name: 'Morning Brief', ...overrides } as CronJob
}

describe('isCronSessionId', () => {
  it('matches the scheduler prefix only', () => {
    expect(isCronSessionId(SESSION)).toBe(true)
    expect(isCronSessionId('rt-abc123')).toBe(false)
    expect(isCronSessionId(null)).toBe(false)
    expect(isCronSessionId(undefined)).toBe(false)
  })
})

describe('isCronRunFinished', () => {
  it('treats an empty transcript as in flight', () => {
    expect(isCronRunFinished(SESSION, [])).toBe(false)
  })

  it('treats a transcript that ends with the cron prompt as in flight', () => {
    expect(isCronRunFinished(SESSION, [message('user')])).toBe(false)
  })

  it('treats a settled assistant reply as finished', () => {
    expect(isCronRunFinished(SESSION, [message('user'), message('assistant')])).toBe(true)
  })

  it('ignores hidden trailing messages and pending assistant placeholders', () => {
    expect(isCronRunFinished(SESSION, [message('user'), message('assistant'), message('system', { hidden: true })])).toBe(
      true
    )
    expect(isCronRunFinished(SESSION, [message('user'), message('assistant', { pending: true })])).toBe(false)
  })

  it('trusts the job record when it names this run with a final status', () => {
    expect(isCronRunFinished(SESSION, [message('user')], [job({ last_run_session_id: SESSION, last_status: 'ok' })])).toBe(
      true
    )
    expect(
      isCronRunFinished(SESSION, [message('user')], [job({ last_run_session_id: SESSION, last_status: 'error' })])
    ).toBe(true)
  })

  it('does not let another run of the same job settle this session', () => {
    expect(
      isCronRunFinished(SESSION, [message('user')], [job({ last_run_session_id: 'cron_other_1', last_status: 'ok' })])
    ).toBe(false)
    expect(isCronRunFinished(SESSION, [message('user')], [job({ last_run_session_id: SESSION, last_status: null })])).toBe(
      false
    )
  })
})
