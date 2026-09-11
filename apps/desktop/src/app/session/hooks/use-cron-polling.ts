import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { getSessionMessages } from '@/hermes'
import { chatMessageArraysEquivalent, toChatMessages } from '@/lib/chat-messages'
import { isCronRunFinished } from '@/lib/cron-run-state'
import { $cronJobs } from '@/store/cron'
import { $awaitingResponse, $busy, $cronSessionInFlight, $messages, setCronSessionInFlight, setMessages } from '@/store/session'

interface UseCronPollingOptions {
  activeSessionId: string | null
  profile?: string
  // Called once the polled transcript shows the run has settled; the caller
  // resumes the session normally so the next turn streams live.
  onRunFinished?: (storedSessionId: string) => void
}

const CRON_POLL_INTERVAL_MS = 1000

/**
 * Refresh the transcript of a cron session whose run is still in flight (AIS-320).
 *
 * The scheduler's agent runs outside the gateway and persists messages only
 * when the turn ends, so the stored snapshot is the only view of such a run.
 * Poll it once a second while the session is marked in flight
 * (`$cronSessionInFlight`, set by the resume path), publish only when the
 * content actually changed — a fresh array per tick re-measures the whole
 * thread and jerks the scroll position — and never while a live turn is
 * streaming into the same view. When the snapshot shows the run finished,
 * stop and hand the session to the normal resume path.
 */
export function useCronPolling({ activeSessionId, profile, onRunFinished }: UseCronPollingOptions) {
  const inFlightSessionId = useStore($cronSessionInFlight)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const inFlightRequestRef = useRef(false)
  const onRunFinishedRef = useRef(onRunFinished)
  onRunFinishedRef.current = onRunFinished

  const target = activeSessionId && inFlightSessionId === activeSessionId ? activeSessionId : null

  useEffect(() => {
    if (!target) {
      return
    }

    let cancelled = false

    const poll = async () => {
      if (cancelled || inFlightRequestRef.current || document.visibilityState !== 'visible') {
        return
      }

      // A live turn owns the view; the stored snapshot lags behind it.
      if ($busy.get() || $awaitingResponse.get()) {
        return
      }

      inFlightRequestRef.current = true

      try {
        const response = await getSessionMessages(target, profile)

        if (cancelled || !response?.messages || $cronSessionInFlight.get() !== target) {
          return
        }

        const next = toChatMessages(response.messages)

        if (!chatMessageArraysEquivalent(next, $messages.get())) {
          setMessages(next)
        }

        if (isCronRunFinished(target, next, $cronJobs.get())) {
          setCronSessionInFlight(current => (current === target ? null : current))
          onRunFinishedRef.current?.(target)
        }
      } catch (err) {
        // Silent fail - don't disrupt the UX if polling fails
        console.debug('[cron-polling] Failed to fetch session transcript:', err)
      } finally {
        inFlightRequestRef.current = false
      }
    }

    void poll()
    pollIntervalRef.current = setInterval(() => {
      void poll()
    }, CRON_POLL_INTERVAL_MS)

    return () => {
      cancelled = true

      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [profile, target])
}
