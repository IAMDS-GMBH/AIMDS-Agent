import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getSessionMessages } from '@/hermes'
import { setCronJobs } from '@/store/cron'
import {
  $cronSessionInFlight,
  $messages,
  setAwaitingResponse,
  setBusy,
  setCronSessionInFlight,
  setMessages
} from '@/store/session'
import type { SessionMessage } from '@/types/hermes'

import { useCronPolling } from './use-cron-polling'

vi.mock('@/hermes', () => ({
  getSessionMessages: vi.fn()
}))

const SESSION = 'cron_aimds-morning-brief_20260911_080024'
const mockedGetSessionMessages = vi.mocked(getSessionMessages)

function stored(role: SessionMessage['role'], content: string, timestamp: number): SessionMessage {
  return { content, role, timestamp } as SessionMessage
}

const PROMPT_ONLY = [stored('user', 'Compose the brief', 1)]
const FINISHED = [stored('user', 'Compose the brief', 1), stored('assistant', '# Morgenbrief', 2)]

function Harness({
  activeSessionId,
  onRunFinished
}: {
  activeSessionId: string | null
  onRunFinished?: (storedSessionId: string) => void
}) {
  useCronPolling({ activeSessionId, onRunFinished })

  return null
}

async function tick(ms = 1000) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useCronPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setMessages([])
    setBusy(false)
    setAwaitingResponse(false)
    setCronSessionInFlight(null)
    setCronJobs([])
    mockedGetSessionMessages.mockReset()
    mockedGetSessionMessages.mockResolvedValue({ messages: PROMPT_ONLY } as never)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does not poll a cron session that is not marked in flight', async () => {
    render(<Harness activeSessionId={SESSION} />)
    await tick(2500)

    expect(mockedGetSessionMessages).not.toHaveBeenCalled()
  })

  it('polls the in-flight session once a second and publishes only when the content changed', async () => {
    setCronSessionInFlight(SESSION)
    render(<Harness activeSessionId={SESSION} />)
    await tick(0)

    expect(mockedGetSessionMessages).toHaveBeenCalledTimes(1)
    const first = $messages.get()
    expect(first).toHaveLength(1)

    await tick(1000)
    await tick(1000)

    expect(mockedGetSessionMessages).toHaveBeenCalledTimes(3)
    // Identical snapshots must not produce a new array (that re-measures the thread and jerks the scroll).
    expect($messages.get()).toBe(first)
  })

  it('never overwrites the view while a live turn is streaming', async () => {
    setCronSessionInFlight(SESSION)
    setBusy(true)
    render(<Harness activeSessionId={SESSION} />)
    await tick(2500)

    expect(mockedGetSessionMessages).not.toHaveBeenCalled()

    setBusy(false)
    setAwaitingResponse(true)
    await tick(1000)

    expect(mockedGetSessionMessages).not.toHaveBeenCalled()
  })

  it('stops and reports the run finished once the snapshot ends with an assistant reply', async () => {
    setCronSessionInFlight(SESSION)
    const onRunFinished = vi.fn()
    render(<Harness activeSessionId={SESSION} onRunFinished={onRunFinished} />)
    await tick(0)

    expect(onRunFinished).not.toHaveBeenCalled()

    mockedGetSessionMessages.mockResolvedValue({ messages: FINISHED } as never)
    await tick(1000)

    expect(onRunFinished).toHaveBeenCalledWith(SESSION)
    expect($cronSessionInFlight.get()).toBeNull()
    expect($messages.get()).toHaveLength(2)

    const calls = mockedGetSessionMessages.mock.calls.length
    await tick(3000)

    expect(mockedGetSessionMessages).toHaveBeenCalledTimes(calls)
  })

  it('treats the job record with a final status as finished even before the reply is stored', async () => {
    setCronSessionInFlight(SESSION)
    setCronJobs([{ id: 'aimds-morning-brief', last_run_session_id: SESSION, last_status: 'ok', name: 'Morning Brief' }] as never)
    const onRunFinished = vi.fn()
    render(<Harness activeSessionId={SESSION} onRunFinished={onRunFinished} />)
    await tick(0)

    expect(onRunFinished).toHaveBeenCalledWith(SESSION)
  })

  it('stops polling when the user navigates to another session', async () => {
    setCronSessionInFlight(SESSION)
    const { rerender } = render(<Harness activeSessionId={SESSION} />)
    await tick(0)
    expect(mockedGetSessionMessages).toHaveBeenCalledTimes(1)

    rerender(<Harness activeSessionId="rt-other" />)
    await tick(3000)

    expect(mockedGetSessionMessages).toHaveBeenCalledTimes(1)
  })
})
