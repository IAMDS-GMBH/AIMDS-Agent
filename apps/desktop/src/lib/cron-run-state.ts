import type { CronJob } from '@/types/hermes'

import type { ChatMessage } from './chat-messages'

export const CRON_SESSION_PREFIX = 'cron_'

export function isCronSessionId(sessionId: null | string | undefined): boolean {
  return typeof sessionId === 'string' && sessionId.startsWith(CRON_SESSION_PREFIX)
}

export type CronRunOutcome = 'error' | 'ok' | 'running'

// Outcome of the cron run behind `sessionId` (AIS-320, AIS-332).
//
// The backend has no "running" flag for a job and never closes the cron
// session row, so this reads the two signals that do exist:
// - the job record points at the newest run (`last_run_session_id`) and
//   carries its `last_status` once the run completed — `ok` or `error`;
// - the scheduler persists a run's messages only when the turn ends, so a
//   stored transcript whose last visible message is a settled assistant reply
//   means the run is over (an in-flight run has the user prompt at most).
//
// `error` is reported separately from `ok`: a failed run (provider
// unreachable, timeout) leaves a transcript with nothing but the prompt, and
// treating it as a plain finished session rendered an empty chat with no hint
// of the failure (SUP-20260914-063903).
export function cronRunOutcome(
  sessionId: string,
  messages: readonly ChatMessage[],
  jobs?: readonly CronJob[] | null
): CronRunOutcome {
  const job = jobs?.find(candidate => candidate.last_run_session_id === sessionId)

  if (job?.last_status === 'ok' || job?.last_status === 'error') {
    return job.last_status
  }

  const visible = messages.filter(message => !message.hidden)
  const last = visible[visible.length - 1]

  return last && last.role === 'assistant' && !last.pending ? 'ok' : 'running'
}

// Whether a cron run behind `sessionId` has finished (AIS-320).
//
// A finished cron session resumes like any other session (live stream, tool
// cards, busy state); only an in-flight one is shown from its stored snapshot
// and polled until it settles.
export function isCronRunFinished(
  sessionId: string,
  messages: readonly ChatMessage[],
  jobs?: readonly CronJob[] | null
): boolean {
  return cronRunOutcome(sessionId, messages, jobs) !== 'running'
}

// The failed cron run behind `sessionId` whose transcript holds no assistant
// reply — the case the error banner exists for. `null` otherwise.
export function failedCronRunJob(
  sessionId: null | string | undefined,
  messages: readonly ChatMessage[],
  jobs?: readonly CronJob[] | null
): CronJob | null {
  if (!sessionId || !isCronSessionId(sessionId)) {
    return null
  }

  if (cronRunOutcome(sessionId, messages, jobs) !== 'error') {
    return null
  }

  const hasReply = messages.some(message => !message.hidden && message.role === 'assistant' && !message.pending)

  if (hasReply) {
    return null
  }

  return jobs?.find(candidate => candidate.last_run_session_id === sessionId) ?? null
}
