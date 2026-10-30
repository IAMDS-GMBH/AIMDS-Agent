import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { FOUR_OPEN_POINTS } from '@/lib/clarify.fixtures'

import {
  $clarifyDrafts,
  $clarifyRequest,
  $clarifyRequests,
  $questionCardFocus,
  $questionCardMinimized,
  type ClarifyRequest,
  clearClarifyRequest,
  requestQuestions,
  revealQuestionCard,
  setClarifyRequest,
  setQuestionCardMinimized,
  setQuestionDraft
} from './clarify'
import { $activeSessionId } from './session'

function clarify(sessionId: string | null, requestId: string): ClarifyRequest {
  return {
    requestId,
    question: `question-${requestId}`,
    choices: null,
    sessionId
  }
}

describe('clarify store', () => {
  beforeEach(() => {
    $clarifyRequests.set({})
    $activeSessionId.set(null)
  })

  afterEach(() => {
    $clarifyRequests.set({})
    $activeSessionId.set(null)
  })

  it('keeps clarify requests from concurrent sessions independent', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    expect($clarifyRequests.get()['session-a']?.requestId).toBe('req-a')
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('req-b')
  })

  it('exposes only the active session via the focus-scoped view', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    $activeSessionId.set('session-a')
    expect($clarifyRequest.get()?.requestId).toBe('req-a')

    $activeSessionId.set('session-b')
    expect($clarifyRequest.get()?.requestId).toBe('req-b')

    $activeSessionId.set('session-c')
    expect($clarifyRequest.get()).toBeNull()
  })

  it('clears only the targeted session, leaving the other pending', () => {
    setClarifyRequest(clarify('session-a', 'req-a'))
    setClarifyRequest(clarify('session-b', 'req-b'))

    clearClarifyRequest('req-a', 'session-a')

    expect($clarifyRequests.get()['session-a']).toBeUndefined()
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('req-b')
  })

  it('ignores a stale clear whose request id no longer matches', () => {
    setClarifyRequest(clarify('session-a', 'req-a2'))

    clearClarifyRequest('req-a1', 'session-a')

    expect($clarifyRequests.get()['session-a']?.requestId).toBe('req-a2')
  })

  it('clears by request id across sessions when no session hint is given', () => {
    setClarifyRequest(clarify('session-a', 'shared'))
    setClarifyRequest(clarify('session-b', 'other'))

    clearClarifyRequest('shared')

    expect($clarifyRequests.get()['session-a']).toBeUndefined()
    expect($clarifyRequests.get()['session-b']?.requestId).toBe('other')
  })
})

// AIS-793: several questions per request, answered on the card.
describe('clarify store with several questions', () => {
  afterEach(() => {
    $clarifyRequests.set({})
    $clarifyDrafts.set({})
    $questionCardMinimized.set({})
    $questionCardFocus.set(null)
    $activeSessionId.set(null)
  })

  it('holds every question of a request; the single form is one', () => {
    setClarifyRequest({ ...clarify('session-a', 'four'), questions: FOUR_OPEN_POINTS })
    setClarifyRequest({ ...clarify('session-b', 'one'), choices: ['A', 'B'] })

    expect(requestQuestions($clarifyRequests.get()['session-a']!).map(q => q.topic)).toEqual([
      'QS-38',
      'QS-41',
      'Release 0.7.15',
      'Docs'
    ])
    expect(requestQuestions($clarifyRequests.get()['session-b']!)).toEqual([
      { question: 'question-one', choices: ['A', 'B'] }
    ])
  })

  it('keeps answers per request and question until the request is gone', () => {
    setClarifyRequest({ ...clarify('session-a', 'four'), questions: FOUR_OPEN_POINTS })
    setQuestionDraft('four', 0, { answer: { response: 'Before the release', state: 'answered' } })
    setQuestionDraft('four', 3, { text: 'In the wi' })
    setQuestionDraft('four', 3, { text: 'In the wiki' })
    setQuestionCardMinimized('four', true)

    expect($clarifyDrafts.get().four?.[0]?.answer?.response).toBe('Before the release')
    expect($clarifyDrafts.get().four?.[3]).toEqual({ answer: null, text: 'In the wiki' })

    clearClarifyRequest('four', 'session-a')

    expect($clarifyDrafts.get().four).toBeUndefined()
    expect($questionCardMinimized.get().four).toBeUndefined()
  })

  it('reveal brings a minimised card back and asks it to take focus', () => {
    setQuestionCardMinimized('four', true)

    revealQuestionCard('four')
    revealQuestionCard('four')

    expect($questionCardMinimized.get().four).toBe(false)
    expect($questionCardFocus.get()).toEqual({ requestId: 'four', tick: 2 })
  })
})
