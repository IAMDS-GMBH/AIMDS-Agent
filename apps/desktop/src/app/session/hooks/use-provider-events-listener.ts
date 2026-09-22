import { useEffect, useRef } from 'react'

export interface SuiteAuthNeedsReauthEvent {
  type: 'suite_auth_needs_reauth'
  provider: string
  label: string
  domain: string
  http_status: number | null
  timestamp: string
}

/**
 * Hook to listen for provider-health events from the backend (AIS-394).
 *
 * Connects to /api/events?channel=provider_events and notifies the callback
 * when a Suite environment's key is found to need re-authentication. Mirrors
 * useCronCompletionListener's connect/reconnect shape for the sibling
 * "cron_events" channel.
 */
export function useProviderEventsListener(
  onSuiteAuthNeedsReauth?: (event: SuiteAuthNeedsReauthEvent) => Promise<void> | void,
  profile?: string
) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    if (!onSuiteAuthNeedsReauth) {
      return
    }

    async function resolveEventsWsUrl(): Promise<string> {
      const desktop = window.hermesDesktop

      if (desktop?.getConnection) {
        const conn = await desktop.getConnection(profile ?? null)
        const base = new URL(conn.baseUrl.endsWith('/') ? conn.baseUrl : `${conn.baseUrl}/`)
        const wsUrl = new URL('api/events', base)
        wsUrl.protocol = wsUrl.protocol === 'https:' ? 'wss:' : 'ws:'
        wsUrl.searchParams.set('channel', 'provider_events')

        if (conn.token) {
          wsUrl.searchParams.set('token', conn.token)
        }

        return wsUrl.toString()
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'

      return `${protocol}//${window.location.host}/api/events?channel=provider_events`
    }

    async function connect() {
      const wsUrl = await resolveEventsWsUrl()

      try {
        const ws = new WebSocket(wsUrl)

        ws.onopen = () => {
          console.debug('[provider-events] WebSocket connected')
        }

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as SuiteAuthNeedsReauthEvent

            if (data.type === 'suite_auth_needs_reauth' && onSuiteAuthNeedsReauth) {
              console.info(`[provider-events] ${data.provider} needs re-authentication`)
              void onSuiteAuthNeedsReauth(data)
            }
          } catch (e) {
            console.error('[provider-events] Failed to parse message:', e)
          }
        }

        ws.onerror = () => {
          console.error('[provider-events] WebSocket error')
        }

        ws.onclose = () => {
          console.debug('[provider-events] WebSocket disconnected, will reconnect in 3s')
          wsRef.current = null

          reconnectTimeoutRef.current = setTimeout(() => {
            void connect()
          }, 3000)
        }

        wsRef.current = ws
      } catch (e) {
        console.error('[provider-events] Failed to create WebSocket:', e)
        reconnectTimeoutRef.current = setTimeout(() => {
          void connect()
        }, 3000)
      }
    }

    void connect()

    return () => {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
    }
  }, [onSuiteAuthNeedsReauth, profile])
}
