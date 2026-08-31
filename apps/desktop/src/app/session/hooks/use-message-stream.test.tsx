/**
 * AIS-275: session.rotated handling and the bounded busy-escape.
 *
 * Context compression rotates the SQLite session id server-side; the gateway
 * emits session.rotated so the client can remap its stored id / route. A
 * terminal running:false with no assistant payload used to leave the busy
 * timer spinning forever when the stream was dropped (transport detach) —
 * the escape clears it after a grace window and backfills the transcript.
 */
import { cleanup, render } from '@testing-library/react'
import type { QueryClient } from '@tanstack/react-query'
import type { MutableRefObject } from 'react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '../../types'

import { useMessageStream } from './use-message-stream'

type GatewayHandler = (event: {
  type: string
  session_id?: string
  payload?: Record<string, unknown>
}) => void

interface HarnessResult {
  handleGatewayEvent: GatewayHandler
}

function baseState(): ClientSessionState {
  return {
    awaitingResponse: true,
    busy: true,
    sawAssistantPayload: false,
    turnStartedAt: Date.now(),
    streamId: null,
    pendingBranchGroup: null,
    needsInput: false,
    messages: []
  } as unknown as ClientSessionState
}

function makeHarness(overrides: { onSessionRotated?: (o: string, n: string, sid: string) => void } = {}) {
  const states = new Map<string, ClientSessionState>()
  states.set('sid-1', baseState())

  const hydrateFromStoredSession = vi.fn(async () => undefined)
  const captured: { current: HarnessResult | null } = { current: null }
  const activeSessionIdRef: MutableRefObject<string | null> = { current: 'sid-1' }

  const updateSessionState = (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState
  ): ClientSessionState => {
    const prev = states.get(sessionId) ?? baseState()
    const next = updater(prev)
    states.set(sessionId, next)

    return next
  }

  function Harness() {
    captured.current = useMessageStream({
      activeSessionIdRef,
      hydrateFromStoredSession,
      onSessionRotated: overrides.onSessionRotated,
      queryClient: { invalidateQueries: vi.fn() } as unknown as QueryClient,
      refreshHermesConfig: vi.fn(async () => undefined),
      refreshSessions: vi.fn(async () => undefined),
      updateSessionState
    })

    return null
  }

  render(<Harness />)

  return { captured, hydrateFromStoredSession, states }
}

describe('useMessageStream (AIS-275)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('forwards session.rotated to the controller callback', () => {
    const onSessionRotated = vi.fn()
    const { captured } = makeHarness({ onSessionRotated })

    act(() => {
      captured.current!.handleGatewayEvent({
        type: 'session.rotated',
        session_id: 'sid-1',
        payload: { old_session_key: 'db-old', new_session_key: 'db-new' }
      })
    })

    expect(onSessionRotated).toHaveBeenCalledWith('db-old', 'db-new', 'sid-1')
  })

  it('clears a stuck busy state after the escape window and backfills', () => {
    const { captured, hydrateFromStoredSession, states } = makeHarness()

    act(() => {
      captured.current!.handleGatewayEvent({
        type: 'session.info',
        session_id: 'sid-1',
        payload: { running: false }
      })
    })

    // The guard keeps the state untouched at first…
    expect(states.get('sid-1')!.busy).toBe(true)

    act(() => {
      vi.advanceTimersByTime(10_001)
    })

    // …but the bounded escape clears it and backfills the transcript.
    const state = states.get('sid-1')!

    expect(state.busy).toBe(false)
    expect(state.awaitingResponse).toBe(false)
    expect(state.turnStartedAt).toBeNull()
    expect(hydrateFromStoredSession).toHaveBeenCalledWith(2, undefined, 'sid-1')
  })

  it('disarms the escape when assistant traffic arrives', () => {
    const { captured, hydrateFromStoredSession } = makeHarness()

    act(() => {
      captured.current!.handleGatewayEvent({
        type: 'session.info',
        session_id: 'sid-1',
        payload: { running: false }
      })
      captured.current!.handleGatewayEvent({
        type: 'message.start',
        session_id: 'sid-1',
        payload: { id: 'm1' }
      })
    })

    act(() => {
      vi.advanceTimersByTime(20_000)
    })

    expect(hydrateFromStoredSession).not.toHaveBeenCalled()
  })
})
