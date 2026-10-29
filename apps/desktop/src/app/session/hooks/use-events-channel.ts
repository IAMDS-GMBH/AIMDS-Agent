import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $connection } from '@/store/session'

/**
 * One backend event channel (`/api/events?channel=<name>`) over a WebSocket,
 * shared by the cron-completion and provider-events listeners.
 *
 * AIS-755: both listeners used to resolve the backend once on mount. After a
 * failed first boot `getConnection()` rejected outside any try, no retry was
 * scheduled, and the listener stayed dead even after gateway-recovery
 * (AIS-749) brought the backend back. Now the hook follows the connection the
 * boot/recovery path publishes (`$connection`): it subscribes when one is
 * established, re-subscribes when a recovery publishes a different backend,
 * and a resolve failure retries like a dropped socket. Exactly one socket per
 * hook: the previous one is closed (without its reconnect) before the next
 * opens.
 */

export const EVENTS_CHANNEL_RETRY_MS = 3_000

interface EventsSocket {
  close: () => void
  onclose: ((event: unknown) => void) | null
  onerror: ((event: unknown) => void) | null
  onmessage: ((event: { data: unknown }) => void) | null
  onopen: ((event: unknown) => void) | null
}

export interface EventsChannelOptions {
  /** Log prefix, e.g. "cron-completion". */
  name: string
  resolveUrl: () => Promise<string>
  onMessage: (data: unknown) => void
  createSocket?: (url: string) => EventsSocket
  retryMs?: number
}

/** Open the channel and keep it open until the returned stop() is called. */
export function openEventsChannel({
  createSocket = url => new WebSocket(url) as unknown as EventsSocket,
  name,
  onMessage,
  resolveUrl,
  retryMs = EVENTS_CHANNEL_RETRY_MS
}: EventsChannelOptions): () => void {
  let stopped = false
  let socket: EventsSocket | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null

  const scheduleRetry = () => {
    if (stopped || retryTimer !== null) {
      return
    }

    retryTimer = setTimeout(() => {
      retryTimer = null
      void connect()
    }, retryMs)
  }

  async function connect() {
    if (stopped) {
      return
    }

    let url: string

    try {
      url = await resolveUrl()
    } catch (error) {
      console.debug(`[${name}] backend not reachable yet, retrying`, error)
      scheduleRetry()

      return
    }

    if (stopped) {
      return
    }

    try {
      const ws = createSocket(url)

      ws.onopen = () => console.debug(`[${name}] WebSocket connected`)

      ws.onmessage = event => {
        try {
          onMessage(JSON.parse(String(event.data)))
        } catch (error) {
          console.error(`[${name}] Failed to parse message:`, error)
        }
      }

      ws.onerror = () => console.error(`[${name}] WebSocket error`)

      ws.onclose = () => {
        if (socket === ws) {
          socket = null
        }

        console.debug(`[${name}] WebSocket disconnected, will reconnect`)
        scheduleRetry()
      }

      socket = ws
    } catch (error) {
      console.error(`[${name}] Failed to create WebSocket:`, error)
      scheduleRetry()
    }
  }

  void connect()

  return () => {
    stopped = true

    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }

    const ws = socket
    socket = null

    if (ws) {
      // A close we asked for is no drop: no reconnect from its onclose.
      ws.onclose = null
      ws.onmessage = null
      ws.close()
    }
  }
}

/** ws(s)://<backend>/api/events?channel=<channel>[&token=…] */
export async function resolveEventsWsUrl(channel: string, profile?: string): Promise<string> {
  const desktop = window.hermesDesktop

  if (desktop?.getConnection) {
    const conn = await desktop.getConnection(profile ?? null)
    const base = new URL(conn.baseUrl.endsWith('/') ? conn.baseUrl : `${conn.baseUrl}/`)
    const wsUrl = new URL('api/events', base)
    wsUrl.protocol = wsUrl.protocol === 'https:' ? 'wss:' : 'ws:'
    wsUrl.searchParams.set('channel', channel)

    if (conn.token) {
      wsUrl.searchParams.set('token', conn.token)
    }

    return wsUrl.toString()
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'

  return `${protocol}//${window.location.host}/api/events?channel=${encodeURIComponent(channel)}`
}

/**
 * Subscribe `onEvent` to a backend event channel for as long as a backend
 * connection is established (see the module comment). The handler may change
 * identity freely; only the channel, the profile and the connection decide
 * when to re-subscribe.
 */
export function useEventsChannel(
  channel: string,
  name: string,
  onEvent: ((data: unknown) => void) | undefined,
  profile?: string
) {
  const connection = useStore($connection)
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  const enabled = Boolean(onEvent)
  const desktop = typeof window !== 'undefined' && Boolean(window.hermesDesktop?.getConnection)
  // In the desktop app the boot/recovery path publishes the connection; the
  // browser build talks to its own origin and needs no signal.
  const connectionKey = desktop ? (connection ? `${connection.baseUrl}\n${connection.token ?? ''}` : null) : 'origin'

  useEffect(() => {
    if (!enabled || connectionKey === null) {
      return
    }

    return openEventsChannel({
      name,
      onMessage: data => handlerRef.current?.(data),
      resolveUrl: () => resolveEventsWsUrl(channel, profile)
    })
  }, [channel, connectionKey, enabled, name, profile])
}
