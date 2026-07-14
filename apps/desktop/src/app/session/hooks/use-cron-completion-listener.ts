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
  onJobCompleted?: (jobId: string, success: boolean, error?: string) => void,
  profile?: string
) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    // Only listen if there are callbacks registered
    if (!onJobCompleted) {
      return
    }

    async function resolveEventsWsUrl(): Promise<string> {
      const desktop = window.hermesDesktop
      if (desktop?.getConnection) {
        const conn = await desktop.getConnection(profile ?? null)
        const base = new URL(conn.baseUrl.endsWith('/') ? conn.baseUrl : `${conn.baseUrl}/`)
        const wsUrl = new URL('api/events', base)
        wsUrl.protocol = wsUrl.protocol === 'https:' ? 'wss:' : 'ws:'
        wsUrl.searchParams.set('channel', 'cron_events')
        if (conn.token) {
          wsUrl.searchParams.set('token', conn.token)
        }
        return wsUrl.toString()
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${protocol}//${window.location.host}/api/events?channel=cron_events`
    }

    async function connect() {
      const wsUrl = await resolveEventsWsUrl()

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
            void connect()
          }, 3000)
        }

        wsRef.current = ws
      } catch (e) {
        console.error('[cron-completion] Failed to create WebSocket:', e)
        reconnectTimeoutRef.current = setTimeout(() => {
          void connect()
        }, 3000)
      }
    }

    void connect()

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
  }, [onJobCompleted, profile])
}
