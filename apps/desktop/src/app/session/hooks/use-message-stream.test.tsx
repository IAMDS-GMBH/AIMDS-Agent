import type { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ChatMessagePart } from '@/lib/chat-messages'
import type { RpcEvent } from '@/types/hermes'

import type { ClientSessionState } from '../../types'

import { useMessageStream } from './use-message-stream'

const SESSION_ID = 'runtime-session-1'

function baseState(): ClientSessionState {
  return {
    storedSessionId: null,
    messages: [],
    branch: '',
    cwd: '',
    model: '',
    provider: '',
    reasoningEffort: '',
    serviceTier: '',
    fast: false,
    yolo: false,
    personality: '',
    busy: false,
    awaitingResponse: false,
    streamId: null,
    sawAssistantPayload: false,
    pendingBranchGroup: null,
    interrupted: false,
    needsInput: false,
    turnStartedAt: null
  }
}

interface HarnessProps {
  onReady: (api: ReturnType<typeof useMessageStream>) => void
  updateSessionState: (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState
  ) => ClientSessionState
}

function Harness({ onReady, updateSessionState }: HarnessProps) {
  const activeSessionIdRef = useRef<string | null>(SESSION_ID)

  const api = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession: async () => undefined,
    queryClient: { invalidateQueries: vi.fn(async () => undefined) } as unknown as QueryClient,
    refreshHermesConfig: async () => undefined,
    refreshSessions: async () => undefined,
    updateSessionState
  })

  useEffect(() => {
    onReady(api)
  }, [api, onReady])

  return null
}

function assistantText(parts: ChatMessagePart[]): string {
  return parts
    .filter((part): part is Extract<ChatMessagePart, { type: 'text' }> => part.type === 'text')
    .map(part => part.text)
    .join('\n')
}

describe('useMessageStream', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('preserves streamed assistant context when message.complete has empty text and clarify is pending', () => {
    const states = new Map<string, ClientSessionState>()

    const updateSessionState = (sessionId: string, updater: (state: ClientSessionState) => ClientSessionState) => {
      const current = states.get(sessionId) ?? baseState()
      const next = updater(current)
      states.set(sessionId, next)

      return next
    }

    let api: ReturnType<typeof useMessageStream> | null = null

    render(
      <Harness
        onReady={value => {
          api = value
        }}
        updateSessionState={updateSessionState}
      />
    )

    const contextLine = "I couldn't find a saved profile yet, so I'm starting onboarding now."

    act(() => {
      api!.handleGatewayEvent({ type: 'message.start', session_id: SESSION_ID, payload: {} } as RpcEvent)
      api!.handleGatewayEvent({ type: 'message.delta', session_id: SESSION_ID, payload: { text: contextLine } } as RpcEvent)
      api!.handleGatewayEvent(
        {
          type: 'tool.start',
          session_id: SESSION_ID,
          payload: {
            name: 'clarify',
            tool_id: 'clarify-1',
            args: { question: 'What is your role/title?' }
          }
        } as RpcEvent
      )
      api!.handleGatewayEvent({ type: 'message.complete', session_id: SESSION_ID, payload: { text: '' } } as RpcEvent)
    })

    const state = states.get(SESSION_ID)
    const assistant = state?.messages.find(message => message.role === 'assistant')

    expect(assistant).toBeDefined()
    expect(assistantText(assistant!.parts)).toContain(contextLine)
    expect(assistant!.parts.some(part => part.type === 'tool-call')).toBe(true)
  })

  it('keeps already-streamed assistant text when a late-turn error banner arrives', () => {
    const states = new Map<string, ClientSessionState>()

    const updateSessionState = (sessionId: string, updater: (state: ClientSessionState) => ClientSessionState) => {
      const current = states.get(sessionId) ?? baseState()
      const next = updater(current)
      states.set(sessionId, next)

      return next
    }

    let api: ReturnType<typeof useMessageStream> | null = null

    render(
      <Harness
        onReady={value => {
          api = value
        }}
        updateSessionState={updateSessionState}
      />
    )

    const progressLine = 'Ich suche jetzt nach dem Ticket im AIS-Board...'
    const errorBanner = 'HTTP 400: ContextWindowExceededError: prompt exceeds model context length'

    act(() => {
      api!.handleGatewayEvent({ type: 'message.start', session_id: SESSION_ID, payload: {} } as RpcEvent)
      api!.handleGatewayEvent({ type: 'message.delta', session_id: SESSION_ID, payload: { text: progressLine } } as RpcEvent)
      api!.handleGatewayEvent(
        { type: 'message.complete', session_id: SESSION_ID, payload: { text: errorBanner } } as RpcEvent
      )
    })

    const state = states.get(SESSION_ID)
    const assistant = state?.messages.find(message => message.role === 'assistant')

    expect(assistant).toBeDefined()
    // The already-visible progress text must survive — only the error banner
    // itself is deduped away, not unrelated prior content (#20xxx regression:
    // a late context-window-overflow error was wiping the whole bubble).
    expect(assistantText(assistant!.parts)).toContain(progressLine)
    expect(assistant!.error).toBe(errorBanner)
  })
})
