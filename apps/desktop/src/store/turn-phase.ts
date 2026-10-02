import { map } from 'nanostores'

/** What a turn is doing before its first output (AIS-460, from `turn.phase`). */
export type TurnPhaseName = 'compressing' | 'retrying' | 'switching_model' | 'waiting'

export interface TurnPhase {
  attempt?: number
  maxAttempts?: number
  /** History size in messages, for "reading the previous conversation". */
  messages?: number
  name: TurnPhaseName
  reason?: 'busy' | 'connection' | 'error'
  /** Last retry reason, kept while the next attempt waits. */
  retriedFor?: 'busy' | 'connection' | 'error'
}

const PHASES = new Set<TurnPhaseName>(['compressing', 'retrying', 'switching_model', 'waiting'])
const REASONS = new Set(['busy', 'connection', 'error'])

const count = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined)

export function parseTurnPhase(payload: unknown, previous?: TurnPhase): TurnPhase | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const row = payload as Record<string, unknown>
  const name = row.phase

  if (typeof name !== 'string' || !PHASES.has(name as TurnPhaseName)) {
    return null
  }

  const reason = typeof row.reason === 'string' && REASONS.has(row.reason) ? (row.reason as TurnPhase['reason']) : undefined

  const phase: TurnPhase = {
    attempt: count(row.attempt),
    maxAttempts: count(row.max_attempts),
    messages: count(row.messages) ?? previous?.messages,
    name: name as TurnPhaseName,
    reason
  }

  if (name === 'retrying') {
    phase.retriedFor = reason
  } else if (name === 'waiting' && (phase.attempt ?? 1) > 1) {
    phase.retriedFor = previous?.retriedFor ?? previous?.reason
  }

  return phase
}

export const $turnPhaseBySession = map<Record<string, TurnPhase | undefined>>({})

export function setTurnPhase(sessionId: string | null | undefined, phase: TurnPhase | null) {
  if (!sessionId) {
    return
  }

  const current = $turnPhaseBySession.get()[sessionId]

  if (!phase && !current) {
    return
  }

  $turnPhaseBySession.setKey(sessionId, phase ?? undefined)
}

export function applyTurnPhaseEvent(sessionId: string | null | undefined, payload: unknown) {
  if (!sessionId) {
    return
  }

  const parsed = parseTurnPhase(payload, $turnPhaseBySession.get()[sessionId])

  if (parsed) {
    setTurnPhase(sessionId, parsed)
  }
}
