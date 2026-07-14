import { useEffect, useRef } from 'react'

type CronCompletionEvent = {
  type: 'cron_job_completed'
  job_id: string
  success: boolean
  error?: string
  timestamp: string
}

/**
 * Hook to listen for cron job completion events from the backend.
 * 
 * Connects to /api/events?channel=cron_events and notifies callbacks
 * when a job completes, allowing the UI to refresh without waiting
 * for the next poll interval.
 */
export function useCronCompletionListener(
  onJobCompleted?: (jobId: string, success: boolean, error?: string) => void
) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    // Only listen if there are callbacks registered
    if (!onJobCompleted) {
      return
    }

    function connect() {
      // Build WebSocket URL - use the same base as the fetch API
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/api/events?channel=cron_events`

      try {
        const ws = new WebSocket(wsUrl)

        ws.onopen = () => {
          console.debug('[cron-completion] WebSocket connected')
        }

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as CronCompletionEvent

            if (data.type === 'cron_job_completed' && onJobCompleted) {
              console.info(
                `[cron-completion] Job ${data.job_id} completed: ${data.success ? 'success' : 'failed'}`
              )
              onJobCompleted(data.job_id, data.success, data.error)
            }
          } catch (e) {
            console.error('[cron-completion] Failed to parse message:', e)
          }
        }

        ws.onerror = () => {
          console.error('[cron-completion] WebSocket error')
        }

        ws.onclose = () => {
          console.debug('[cron-completion] WebSocket disconnected, will reconnect in 3s')
          wsRef.current = null

          // Reconnect after 3 seconds
          reconnectTimeoutRef.current = setTimeout(() => {
            connect()
          }, 3000)
        }

        wsRef.current = ws
      } catch (e) {
        console.error('[cron-completion] Failed to create WebSocket:', e)
        reconnectTimeoutRef.current = setTimeout(() => {
          connect()
        }, 3000)
      }
    }

    connect()

    return () => {
      // Cleanup on unmount
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
    }
  }, [onJobCompleted])
}
