import { useEffect, useRef } from 'react'

import { getSessionMessages } from '@/hermes'
import { toChatMessages } from '@/lib/chat-messages'
import { setMessages } from '@/store/session'

interface UseCronPollingOptions {
  activeSessionId: string | null
  profile?: string
  /**
   * Only poll if the session was created/started recently.
   * Cron jobs that are in-flight will have last_active within this window.
   * Set to 0 to always poll. Default: 5 minutes.
   */
  recentThresholdMs?: number
}

const CRON_POLL_INTERVAL_MS = 1000 // Poll every 1 second for real-time updates
const DEFAULT_RECENT_THRESHOLD_MS = 5 * 60 * 1000 // 5 minutes

/**
 * Auto-poll transcript for active cron sessions.
 *
 * Cron sessions (id starts with "cron_") are started by the backend when manually triggered.
 * This hook polls the session transcript every 3 seconds to show real-time output updates
 * in the UI without requiring manual refresh. Polling stops when the session is no longer
 * active, or when the auto-detect heuristic determines it's no longer in-flight.
 */
export function useCronPolling({
  activeSessionId,
  profile,
  recentThresholdMs = DEFAULT_RECENT_THRESHOLD_MS
}: UseCronPollingOptions) {
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastPollRef = useRef<number>(0)

  useEffect(() => {
    // Only poll cron sessions
    if (!activeSessionId || !activeSessionId.startsWith('cron_')) {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }

      return
    }

    // Set up polling
    const poll = async () => {
      try {
        const now = Date.now()

        // Rate-limit to avoid hammering the backend
        if (now - lastPollRef.current < CRON_POLL_INTERVAL_MS) {
          return
        }

        lastPollRef.current = now

        // Only continue polling if document is visible
        if (document.visibilityState !== 'visible') {
          return
        }

        const response = await getSessionMessages(activeSessionId, profile)

        if (response?.messages) {
          setMessages(toChatMessages(response.messages))
        }
      } catch (err) {
        // Silent fail - don't disrupt the UX if polling fails
        console.debug('[cron-polling] Failed to fetch session transcript:', err)
      }
    }

    // Initial poll
    void poll()

    // Set up interval
    pollIntervalRef.current = setInterval(() => {
      void poll()
    }, CRON_POLL_INTERVAL_MS)

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [activeSessionId, profile])
}
