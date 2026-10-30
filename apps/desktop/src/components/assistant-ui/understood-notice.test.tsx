import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'
import { type ChatMessage, toChatMessages } from '@/lib/chat-messages'
import { toRuntimeMessage } from '@/lib/chat-runtime'
import { $liveUnderstood, applyUnderstoodEvent, recordUnderstood, threadUserTurnKey } from '@/store/understood'

import { UnderstoodNotice } from './understood-notice'

const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nProvider configClient={null} initialLocale="en">
    {children}
  </I18nProvider>
)

const QUESTION = 'Compare the three rollout options for the new billing pipeline and recommend one.'

function userMessage(understood?: string): ThreadMessage {
  return {
    attachments: [],
    content: [{ text: QUESTION, type: 'text' }],
    createdAt: new Date('2026-10-30T09:00:00Z'),
    id: 'user-1',
    metadata: { custom: understood ? { attachmentRefs: [], understood } : { attachmentRefs: [] } },
    role: 'user'
  } as ThreadMessage
}

function Harness({ message, sessionId = 'sid' }: { message: ThreadMessage; sessionId?: string }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    isRunning: false,
    messages: [message],
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <UnderstoodNotice messageIndex={0} sessionId={sessionId} />
    </AssistantRuntimeProvider>
  )
}

const notice = () => document.querySelector('[data-slot="understood-notice"]')
const toggle = () => screen.getByRole('button')

afterEach(cleanup)

describe('UnderstoodNotice (AIS-789)', () => {
  beforeEach(() => $liveUnderstood.set({}))

  it('is one collapsed line by default and opens to the full line and the original question', () => {
    render(<Harness message={userMessage('You want the three options compared and one recommended.')} />, { wrapper })

    expect(toggle().getAttribute('aria-expanded')).toBe('false')
    expect(notice()?.textContent).toContain('Understood')
    expect(document.querySelector('[data-slot="understood-notice-line"]')?.className).toContain('truncate')
    expect(screen.queryByText(QUESTION)).toBeNull()

    fireEvent.click(toggle())

    expect(toggle().getAttribute('aria-expanded')).toBe('true')
    expect(document.querySelector('[data-slot="understood-notice-line"]')?.className).not.toContain('truncate')
    expect(screen.getByRole('region').textContent).toContain('Original question')
    expect(screen.getByText(QUESTION)).toBeTruthy()

    fireEvent.click(toggle())

    expect(toggle().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText(QUESTION)).toBeNull()
  })

  it('closes with Escape', () => {
    render(<Harness message={userMessage('You want X.')} />, { wrapper })

    fireEvent.click(toggle())
    fireEvent.keyDown(toggle(), { key: 'Escape' })

    expect(toggle().getAttribute('aria-expanded')).toBe('false')
  })

  it('shows nothing without an understood line (the deterministic fallback)', () => {
    render(<Harness message={userMessage()} />, { wrapper })

    expect(notice()).toBeNull()
  })

  it('uses a line that arrived live before the transcript has it', () => {
    render(<Harness message={userMessage()} />, { wrapper })

    act(() => recordUnderstood('sid', threadUserTurnKey(QUESTION), 'You want a recommendation.'))

    expect(notice()?.textContent).toContain('You want a recommendation.')
  })

  it('does not take a live line of another session', () => {
    recordUnderstood('other', threadUserTurnKey(QUESTION), 'Someone else.')
    render(<Harness message={userMessage()} />, { wrapper })

    expect(notice()).toBeNull()
  })
})

describe('understood line data path (AIS-789)', () => {
  beforeEach(() => $liveUnderstood.set({}))

  it('loads from the stored transcript into the thread message metadata', () => {
    const [user] = toChatMessages([
      { content: QUESTION, role: 'user', timestamp: 1, understood: ' You want X. ' },
      { content: 'Answer', role: 'assistant', timestamp: 2 }
    ])

    expect(user.understood).toBe('You want X.')
    expect((toRuntimeMessage(user).metadata.custom as { understood?: string }).understood).toBe('You want X.')
  })

  it('a live event lands on the newest user message and in the live store', () => {
    const messages: ChatMessage[] = [
      { id: 'u1', parts: [{ text: 'First', type: 'text' }], role: 'user' },
      { id: 'a1', parts: [{ text: 'Reply', type: 'text' }], role: 'assistant' },
      { attachmentRefs: ['@file:notes.md'], id: 'u2', parts: [{ text: QUESTION, type: 'text' }], role: 'user' }
    ]

    const next = applyUnderstoodEvent('sid', messages, 'You want X.')

    expect(next[2].understood).toBe('You want X.')
    expect(next[0].understood).toBeUndefined()
    expect(Object.values($liveUnderstood.get())).toEqual(['You want X.'])
    expect(applyUnderstoodEvent('sid', [], 'You want X.')).toEqual([])
  })
})
