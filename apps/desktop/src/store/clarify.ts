import { atom, computed } from 'nanostores'

import type { ClarifyAnswer, ClarifyQuestion } from '@/lib/clarify'

import { $activeSessionId } from './session'

export interface ClarifyRequest {
  requestId: string
  question: string
  choices: string[] | null
  sessionId: string | null
  // AIS-333: epoch ms at which the gateway stops waiting (clarify.request
  // `deadline_at`). Drives the countdown in the question card.
  deadlineAt?: number | null
  timeoutSeconds?: number | null
  // AIS-793: several questions in one request; absent for the single form.
  questions?: ClarifyQuestion[]
}

/** Every question of a request — the single form is one. */
export function requestQuestions(request: ClarifyRequest): ClarifyQuestion[] {
  if (request.questions?.length) {
    return request.questions
  }

  return [{ question: request.question, ...(request.choices?.length ? { choices: request.choices } : {}) }]
}

// Pending clarify requests keyed by the runtime session id that raised them.
// Storing per-session (instead of one shared slot) lets a *background* session
// park its clarify request while the user is looking at a different chat, then
// resolve it once they switch over — without a second concurrent clarify
// clobbering the first. A request with no session id lands under the empty key.
const keyFor = (sessionId: string | null | undefined): string => sessionId ?? ''

export const $clarifyRequests = atom<Record<string, ClarifyRequest>>({})

// The clarify request for the currently-viewed session. The question card at
// the composer only ever shows the active session's request, so it reads this
// focus-scoped view rather than reaching into the whole map.
export const $clarifyRequest = computed(
  [$clarifyRequests, $activeSessionId],
  (requests, activeId) => requests[keyFor(activeId)] ?? null
)

// AIS-793: what the user has answered on a card so far, by request id and
// question index — kept outside the card so minimising it or switching chats
// loses nothing. `text` is the free answer being typed.
export interface QuestionDraft {
  answer: ClarifyAnswer | null
  text: string
}

export const $clarifyDrafts = atom<Record<string, Record<number, QuestionDraft>>>({})

// Cards the user minimised to the composer chip, by request id.
export const $questionCardMinimized = atom<Record<string, boolean>>({})

// Bumped to bring the card back and focus its first open question (the
// thread's anchor and the composer chip do this).
export const $questionCardFocus = atom<{ requestId: string; tick: number } | null>(null)

export function setQuestionDraft(requestId: string, index: number, patch: Partial<QuestionDraft>): void {
  const drafts = $clarifyDrafts.get()
  const forRequest = drafts[requestId] ?? {}
  const current = forRequest[index] ?? { answer: null, text: '' }

  $clarifyDrafts.set({ ...drafts, [requestId]: { ...forRequest, [index]: { ...current, ...patch } } })
}

export function setQuestionCardMinimized(requestId: string, minimized: boolean): void {
  $questionCardMinimized.set({ ...$questionCardMinimized.get(), [requestId]: minimized })
}

export function revealQuestionCard(requestId: string): void {
  setQuestionCardMinimized(requestId, false)
  $questionCardFocus.set({ requestId, tick: ($questionCardFocus.get()?.tick ?? 0) + 1 })
}

function dropCardState(requestIds: string[]): void {
  if (!requestIds.length) {
    return
  }

  const drafts = { ...$clarifyDrafts.get() }
  const minimized = { ...$questionCardMinimized.get() }

  for (const id of requestIds) {
    delete drafts[id]
    delete minimized[id]
  }

  $clarifyDrafts.set(drafts)
  $questionCardMinimized.set(minimized)
}

export function setClarifyRequest(request: ClarifyRequest): void {
  $clarifyRequests.set({ ...$clarifyRequests.get(), [keyFor(request.sessionId)]: request })
}

export function clearClarifyRequest(requestId?: string, sessionId?: string | null): void {
  const requests = $clarifyRequests.get()

  // Targeted clear when the caller knows the session (the common path from the
  // question card answering its own request).
  if (sessionId !== undefined) {
    const key = keyFor(sessionId)
    const current = requests[key]

    if (!current || (requestId && current.requestId !== requestId)) {
      return
    }

    const next = { ...requests }
    delete next[key]
    $clarifyRequests.set(next)
    dropCardState([current.requestId])

    return
  }

  // Fallback with no session hint: drop every entry matching the request id
  // (or clear all when none is given).
  const next: Record<string, ClarifyRequest> = {}
  const dropped: string[] = []

  for (const [key, value] of Object.entries(requests)) {
    if (requestId && value.requestId !== requestId) {
      next[key] = value
    } else {
      dropped.push(value.requestId)
    }
  }

  if (dropped.length) {
    $clarifyRequests.set(next)
    dropCardState(dropped)
  }
}
