import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { I18nProvider } from '@/i18n'
import { FOUR_OPEN_POINTS } from '@/lib/clarify.fixtures'
import {
  $clarifyDrafts,
  $clarifyRequests,
  $questionCardFocus,
  $questionCardMinimized,
  type ClarifyRequest,
  revealQuestionCard,
  setClarifyRequest
} from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { $activeSessionId } from '@/store/session'

import { formatCountdown, QuestionCard } from './question-card'

// Pin the locale: DEFAULT_LOCALE is 'de', the assertions below use English copy.
const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nProvider configClient={null} initialLocale="en">
    {children}
  </I18nProvider>
)

const request = vi.fn()

function ask(overrides: Partial<ClarifyRequest> = {}): ClarifyRequest {
  const value: ClarifyRequest = {
    requestId: 'rid-4',
    question: FOUR_OPEN_POINTS[0]!.question,
    choices: FOUR_OPEN_POINTS[0]!.choices ?? null,
    questions: FOUR_OPEN_POINTS,
    sessionId: 'sess-1',
    ...overrides
  }

  setClarifyRequest(value)

  return value
}

beforeEach(() => {
  $activeSessionId.set('sess-1')
  request.mockReset()
  request.mockResolvedValue({ ok: true })
  $gateway.set({ request } as unknown as HermesGateway)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  $clarifyRequests.set({})
  $clarifyDrafts.set({})
  $questionCardMinimized.set({})
  $questionCardFocus.set(null)
  $gateway.set(null)
  $activeSessionId.set(null)
})

describe('formatCountdown', () => {
  it('renders mm:ss and never goes negative', () => {
    expect(formatCountdown(600_000)).toBe('10:00')
    expect(formatCountdown(29_400)).toBe('00:30')
    expect(formatCountdown(-5)).toBe('00:00')
  })
})

describe('QuestionCard', () => {
  it('renders nothing without a pending question', () => {
    const { container } = render(<QuestionCard />, { wrapper })

    expect(container.querySelector('[data-slot="question-card"]')).toBeNull()
  })

  it('shows every question with its topic, choices, own answer and later', () => {
    ask()
    render(<QuestionCard />, { wrapper })

    expect(screen.getByRole('region', { name: '4 questions' })).toBeTruthy()
    expect(screen.getByText('0 of 4 answered')).toBeTruthy()
    expect([...document.querySelectorAll('[data-slot="clarify-topic"]')].map(n => n.textContent)).toEqual([
      'QS-38',
      'QS-41',
      'Release 0.7.15',
      'Docs'
    ])
    expect(screen.getAllByRole('button', { name: 'Later' })).toHaveLength(4)
    expect(screen.getAllByText('Own answer')).toHaveLength(3)
    // The open-ended question takes text right away.
    expect(screen.getAllByPlaceholderText('Type your answer…')).toHaveLength(1)
  })

  it('answers one question at a time and sends them together', async () => {
    ask()
    render(<QuestionCard />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: 'After the release' }))
    expect(screen.getByText('1 of 4 answered')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Change' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Tobias' }))
    // "Later" on the release question; the docs question stays open.
    fireEvent.click(screen.getAllByRole('button', { name: 'Later' })[0]!)
    expect(screen.getByText('3 of 4 answered')).toBeTruthy()

    const send = screen.getByRole('button', { name: 'Send, 1 question later' })
    fireEvent.click(send)

    await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
    expect(request).toHaveBeenCalledWith('clarify.respond', {
      request_id: 'rid-4',
      answer: '',
      answers: [
        { response: 'After the release', state: 'answered' },
        { response: 'Tobias', state: 'answered' },
        { response: '', state: 'skipped' },
        { response: '', state: 'skipped' }
      ]
    })
    await waitFor(() => expect($clarifyRequests.get()['sess-1']).toBeUndefined())
  })

  it('takes an own answer and lets the user change an answer', async () => {
    ask()
    render(<QuestionCard />, { wrapper })

    fireEvent.change(screen.getByPlaceholderText('Type your answer…'), { target: { value: 'Into the wiki' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use answer' }))
    expect(screen.getByText('Into the wiki')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    expect(screen.getByDisplayValue('Into the wiki')).toBeTruthy()
  })

  it('sends a single question at the first click, like before', async () => {
    ask({ questions: undefined, question: 'Which region?', choices: ['Bavaria', 'Berlin'], requestId: 'rid-1' })
    render(<QuestionCard />, { wrapper })

    expect(screen.getByRole('region', { name: 'Question' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Berlin' }))

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('clarify.respond', {
        request_id: 'rid-1',
        answer: 'Berlin',
        answers: [{ response: 'Berlin', state: 'answered' }]
      })
    )
  })

  it('defers a single question with later', async () => {
    ask({ questions: undefined, question: 'Which region?', choices: null, requestId: 'rid-2' })
    render(<QuestionCard />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: 'Later' }))

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('clarify.respond', {
        request_id: 'rid-2',
        answer: '',
        answers: [{ response: '', state: 'skipped' }]
      })
    )
  })

  it('minimises to a chip with the open count and comes back from it', () => {
    ask()
    render(<QuestionCard />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: 'Tobias' }))
    fireEvent.click(screen.getByRole('button', { name: 'Minimize' }))

    expect(document.querySelector('[data-slot="question-card"]')).toBeNull()
    const chip = screen.getByRole('button', { name: '3 open questions' })

    fireEvent.click(chip)
    expect(document.querySelector('[data-slot="question-card"]')).not.toBeNull()
    // The answer survived.
    expect(screen.getByText('1 of 4 answered')).toBeTruthy()
  })

  it('opens again when the thread anchor asks for it', () => {
    const value = ask()
    $questionCardMinimized.set({ [value.requestId]: true })
    render(<QuestionCard />, { wrapper })

    act(() => revealQuestionCard(value.requestId))

    expect(document.querySelector('[data-slot="question-card"]')).not.toBeNull()
  })

  it('only shows the active session’s questions', () => {
    ask({ sessionId: 'sess-2' })
    const { container } = render(<QuestionCard />, { wrapper })

    expect(container.querySelector('[data-slot="question-card"]')).toBeNull()
  })

  it('shows the countdown from the request deadline and turns urgent in the last 30 s', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-14T10:00:00.000Z'))
    ask({ deadlineAt: Date.now() + 65_000, timeoutSeconds: 65 })

    render(<QuestionCard />, { wrapper })

    const countdown = screen.getByText(/Time to answer: 01:05/)
    expect(countdown.closest('[data-slot="clarify-countdown"]')?.getAttribute('data-urgent')).toBeNull()

    act(() => {
      vi.advanceTimersByTime(40_000)
    })

    expect(
      screen
        .getByText(/Answer within 00:25/)
        .closest('[data-slot="clarify-countdown"]')
        ?.getAttribute('data-urgent')
    ).toBe('true')
  })

  it('renders no countdown when the request carries no deadline', () => {
    ask()

    const { container } = render(<QuestionCard />, { wrapper })

    expect(container.querySelector('[data-slot="clarify-countdown"]')).toBeNull()
  })
})
