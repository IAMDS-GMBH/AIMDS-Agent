import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'
import { FOUR_OPEN_POINTS } from '@/lib/clarify.fixtures'
import {
  $clarifyRequests,
  $questionCardFocus,
  $questionCardMinimized,
  setClarifyRequest,
  setQuestionCardMinimized
} from '@/store/clarify'
import { $activeSessionId } from '@/store/session'

import { ClarifyTool, readClarifyTimeout } from './clarify-tool'

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
  $clarifyRequests.set({})
  $questionCardMinimized.set({})
  $questionCardFocus.set(null)
  $activeSessionId.set(null)
})

describe('readClarifyTimeout', () => {
  it('recognises a timed-out result (object or JSON string)', () => {
    expect(readClarifyTimeout({ response_state: 'timeout', timeout_seconds: 600, question: 'Q' })).toEqual({
      timeoutSeconds: 600,
      question: 'Q'
    })
    expect(readClarifyTimeout(JSON.stringify({ reason_code: 'clarify_timeout' }))).toEqual({
      timeoutSeconds: null,
      question: ''
    })
  })

  it('ignores answered results', () => {
    expect(readClarifyTimeout({ response_state: 'answered', user_response: 'Bavaria' })).toBeNull()
    expect(readClarifyTimeout('Bavaria')).toBeNull()
    expect(readClarifyTimeout(undefined)).toBeNull()
  })
})

describe('ClarifyTool while the question is open', () => {
  it('keeps a small anchor in the thread instead of the card', () => {
    const { container } = render(<ClarifyTool {...props({ args: { questions: FOUR_OPEN_POINTS } })} />, { wrapper })

    expect(container.querySelector('[data-slot="clarify-anchor"]')).not.toBeNull()
    expect(container.querySelector('[data-choice]')).toBeNull()
    expect(screen.getByText('Asked 4 questions')).toBeTruthy()
    expect(screen.getByText('QS-38 · QS-41 · Release 0.7.15 · Docs')).toBeTruthy()
  })

  it('brings the minimised card back from the anchor', () => {
    setClarifyRequest({
      requestId: 'rid-4',
      question: FOUR_OPEN_POINTS[0]!.question,
      choices: null,
      questions: FOUR_OPEN_POINTS,
      sessionId: 'sess-1'
    })
    setQuestionCardMinimized('rid-4', true)
    render(<ClarifyTool {...props({ args: { questions: FOUR_OPEN_POINTS } })} />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: 'Go to the questions' }))

    expect($questionCardMinimized.get()['rid-4']).toBe(false)
    expect($questionCardFocus.get()?.requestId).toBe('rid-4')
  })

  it('names a single question', () => {
    render(<ClarifyTool {...props()} />, { wrapper })

    expect(screen.getByText('Asked a question')).toBeTruthy()
    expect(screen.getByText('Which region?')).toBeTruthy()
  })
})

describe('ClarifyTool once answered', () => {
  it('shows question → answer at full strength for several questions', () => {
    const { container } = render(
      <ClarifyTool
        {...props({
          args: { questions: FOUR_OPEN_POINTS },
          result: JSON.stringify({
            v: 1,
            questions: FOUR_OPEN_POINTS,
            answers: [
              { state: 'answered', response: 'After the release' },
              { state: 'answered', response: 'Tobias' },
              { state: 'skipped', response: '' },
              { state: 'timeout', response: '' }
            ]
          })
        })}
      />,
      { wrapper }
    )

    const block = container.querySelector('[data-slot="tool-block"]')
    // The CSS fades tool rows to 0.67 — except clarify (data-tool).
    expect(block?.getAttribute('data-tool')).toBe('clarify')
    expect(container.querySelector('[data-slot="clarify-answered"]')).not.toBeNull()
    expect(screen.getByText('4 questions')).toBeTruthy()
    expect(screen.getByText('Should the load test run before or after the release?')).toBeTruthy()
    expect(screen.getByText('After the release')).toBeTruthy()
    expect(screen.getByText('Tobias')).toBeTruthy()
    expect(screen.getByText('Later').getAttribute('data-state')).toBe('skipped')
    expect(screen.getByText('Time is up').getAttribute('data-state')).toBe('timeout')
  })

  it('reads the single form (and sessions from before the contract)', () => {
    render(
      <ClarifyTool
        {...props({
          result: JSON.stringify({
            question: 'Which region?',
            user_response: 'Bavaria',
            response_state: 'answered',
            resolved: true
          })
        })}
      />,
      { wrapper }
    )

    expect(screen.getByText('Question')).toBeTruthy()
    expect(screen.getByText('Which region?')).toBeTruthy()
    expect(screen.getByText('Bavaria')).toBeTruthy()
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
