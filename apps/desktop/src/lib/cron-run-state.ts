import type { CronJob } from '@/types/hermes'

import type { ChatMessage } from './chat-messages'

export const CRON_SESSION_PREFIX = 'cron_'

export function isCronSessionId(sessionId: null | string | undefined): boolean {
  return typeof sessionId === 'string' && sessionId.startsWith(CRON_SESSION_PREFIX)
}

// Whether a cron run behind `sessionId` has finished (AIS-320).
//
// The backend has no "running" flag for a job and never closes the cron
// session row, so this reads the two signals that do exist:
// - the scheduler persists a run's messages only when the turn ends, so a
//   stored transcript whose last visible message is a settled assistant reply
//   means the run is over (an in-flight run has the user prompt at most);
// - the job record points at the newest run (`last_run_session_id`) and
//   carries its `last_status` once the run completed.
//
// A finished cron session resumes like any other session (live stream, tool
// cards, busy state); only an in-flight one is shown from its stored snapshot
// and polled until it settles.
export function isCronRunFinished(
  sessionId: string,
  messages: readonly ChatMessage[],
  jobs?: readonly CronJob[] | null
): boolean {
  const job = jobs?.find(candidate => candidate.last_run_session_id === sessionId)

  if (job?.last_status === 'ok' || job?.last_status === 'error') {
    return true
  }

  const visible = messages.filter(message => !message.hidden)
  const last = visible[visible.length - 1]

  return Boolean(last && last.role === 'assistant' && !last.pending)
}
