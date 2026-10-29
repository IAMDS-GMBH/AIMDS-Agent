import { useCallback } from 'react'

import { useEventsChannel } from './use-events-channel'

export interface CronCompletionEvent {
  type: 'cron_job_completed'
  job_id: string
  success: boolean
  error?: string
  timestamp: string
  // AIS-305: the scheduler annotates the completion with what it wrote so
  // the desktop can open the artifact without a second round-trip.
  job_name?: null | string
  profile?: null | string
  output_path?: null | string
  output_at?: null | string
  session_id?: null | string
}

/**
 * Hook to listen for cron job completion events from the backend.
 *
 * Connects to /api/events?channel=cron_events and notifies callbacks
 * when a job completes, allowing the UI to refresh without waiting
 * for the next poll interval. Subscribes once a backend connection is
 * established and again after a recovery (AIS-755, see useEventsChannel).
 */
export function useCronCompletionListener(
  onJobCompleted?: (event: CronCompletionEvent) => Promise<void> | void,
  profile?: string
) {
  const onEvent = useCallback(
    (raw: unknown) => {
      const data = raw as CronCompletionEvent | null

      if (data?.type === 'cron_job_completed' && onJobCompleted) {
        console.info(`[cron-completion] Job ${data.job_id} completed: ${data.success ? 'success' : 'failed'}`)
        void onJobCompleted(data)
      }
    },
    [onJobCompleted]
  )

  useEventsChannel('cron_events', 'cron-completion', onJobCompleted ? onEvent : undefined, profile)
}
