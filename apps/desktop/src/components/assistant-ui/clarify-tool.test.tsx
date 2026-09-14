import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $clarifyRequests, setClarifyRequest } from '@/store/clarify'
import { $activeSessionId } from '@/store/session'

import { ClarifyTool, formatCountdown, readClarifyTimeout } from './clarify-tool'

// Pin the locale: DEFAULT_LOCALE is 'de', the assertions below use English copy.
const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nProvider configClient={null} initialLocale="en">
    {children}
  </I18nProvider>
)

function props(overrides: Partial<ToolCallMessagePartProps> = {}): ToolCallMessagePartProps {
  return {
    type: 'tool-call',
    toolCallId: 'call-1',
    toolName: 'clarify',
    args: { question: 'Which region?', choices: ['Bavaria', 'Berlin'] },
    argsText: '',
    status: { type: 'running' },
    addResult: () => undefined,
    ...overrides
  } as unknown as ToolCallMessagePartProps
}

beforeEach(() => {
  $activeSessionId.set('sess-1')
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  $clarifyRequests.set({})
  $activeSessionId.set(null)
})

describe('readClarifyTimeout', () => {
  it('recognises a timed-out result (object or JSON string)', () => {
    expect(readClarifyTimeout({ response_state: 'timeout', timeout_seconds: 600, question: 'Q' })).toEqual({
      timeoutSeconds: 600,
      question: 'Q'
    })
    expect(readClarifyTimeout(JSON.stringify({ reason_code: 'clarify_timeout' }))).toEqual({ timeoutSeconds: null, question: '' })
  })

  it('ignores answered results', () => {
    expect(readClarifyTimeout({ response_state: 'answered', user_response: 'Bavaria' })).toBeNull()
    expect(readClarifyTimeout('Bavaria')).toBeNull()
    expect(readClarifyTimeout(undefined)).toBeNull()
  })
})

describe('formatCountdown', () => {
  it('renders mm:ss and never goes negative', () => {
    expect(formatCountdown(600_000)).toBe('10:00')
    expect(formatCountdown(29_400)).toBe('00:30')
    expect(formatCountdown(-5)).toBe('00:00')
  })
})

describe('ClarifyTool', () => {
  it('shows the countdown from the request deadline and turns urgent in the last 30 s', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-14T10:00:00.000Z'))
    setClarifyRequest({
      requestId: 'rid-1',
      question: 'Which region?',
      choices: ['Bavaria', 'Berlin'],
      sessionId: 'sess-1',
      deadlineAt: Date.now() + 65_000,
      timeoutSeconds: 65
    })

    render(<ClarifyTool {...props()} />, { wrapper })

    const countdown = screen.getByText(/Time to answer: 01:05/)
    expect(countdown).toBeTruthy()
    expect(countdown.closest('[data-slot="clarify-countdown"]')?.getAttribute('data-urgent')).toBeNull()

    act(() => {
      vi.advanceTimersByTime(40_000)
    })

    expect(screen.getByText(/Answer within 00:25/)).toBeTruthy()
    expect(screen.getByText(/Answer within 00:25/).closest('[data-slot="clarify-countdown"]')?.getAttribute('data-urgent')).toBe(
      'true'
    )
  })

  it('renders no countdown when the request carries no deadline', () => {
    setClarifyRequest({ requestId: 'rid-2', question: 'Which region?', choices: null, sessionId: 'sess-1' })

    const { container } = render(<ClarifyTool {...props({ args: { question: 'Which region?' } })} />, { wrapper })

    expect(container.querySelector('[data-slot="clarify-countdown"]')).toBeNull()
  })

  it('keeps an expired question visible as a timed-out card instead of collapsing it', () => {
    render(
      <ClarifyTool
        {...props({
          result: JSON.stringify({
            question: 'Which region?',
            user_response: '',
            response_state: 'timeout',
            resolved: false,
            reason_code: 'clarify_timeout',
            timeout_seconds: 600
          })
        })}
      />,
      { wrapper }
    )

    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.getByText('Time is up')).toBeTruthy()
    expect(screen.getByText(/No answer arrived within 600 s/)).toBeTruthy()
    expect(screen.getByText(/Which region\?/)).toBeTruthy()
  })
})
