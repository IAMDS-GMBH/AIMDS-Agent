import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'
import { $connection } from '@/store/session'

import { useCronCompletionListener } from './use-cron-completion-listener'
import { EVENTS_CHANNEL_RETRY_MS, openEventsChannel } from './use-events-channel'
import { useProviderEventsListener } from './use-provider-events-listener'

// AIS-755: after a failed first boot the cron-completion and provider-events
// listeners never subscribed again, even once gateway-recovery (AIS-749) had
// the backend back. They follow the published connection now, with exactly
// one socket per listener.

class FakeSocket {
  static all: FakeSocket[] = []
  closed = false
  onclose: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onopen: ((event: unknown) => void) | null = null

  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }

  close() {
    this.closed = true
    // Like the browser: onclose fires later, for our own close() too.
    const handler = this.onclose
    queueMicrotask(() => handler?.({}))
  }

  /** The server or the network drops the socket. */
  drop() {
    this.closed = true
    this.onclose?.({})
  }

  static open() {
    return FakeSocket.all.filter(socket => !socket.closed)
  }
}

function connection(baseUrl: string, token = 't0'): HermesConnection {
  return {
    baseUrl,
    isFullscreen: false,
    logs: [],
    nativeOverlayWidth: 0,
    token,
    windowButtonPosition: null,
    wsUrl: `${baseUrl.replace('http', 'ws')}/api/ws`
  }
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('openEventsChannel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeSocket.all = []
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('retries when the backend cannot be resolved yet instead of giving up', async () => {
    const resolveUrl = vi.fn().mockRejectedValueOnce(new Error('backend not ready')).mockResolvedValue('ws://127.0.0.1:9/api/events')
    const stop = openEventsChannel({ createSocket: url => new FakeSocket(url), name: 't', onMessage: vi.fn(), resolveUrl })

    await flush()
    expect(FakeSocket.all).toHaveLength(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS)
    })
    expect(FakeSocket.open()).toHaveLength(1)

    // A dropped socket reconnects; a stopped channel does not.
    FakeSocket.all[0].drop()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS)
    })
    expect(FakeSocket.open()).toHaveLength(1)
    expect(FakeSocket.all).toHaveLength(2)

    stop()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS * 3)
    })
    expect(FakeSocket.open()).toHaveLength(0)
    expect(FakeSocket.all).toHaveLength(2)
  })

  it('does not open a socket when stopped while the URL is still resolving', async () => {
    let resolve: (url: string) => void = () => undefined

    const stop = openEventsChannel({
      createSocket: url => new FakeSocket(url),
      name: 't',
      onMessage: vi.fn(),
      resolveUrl: () => new Promise<string>(r => (resolve = r))
    })

    stop()
    resolve('ws://127.0.0.1:9/api/events')
    await flush()
    expect(FakeSocket.all).toHaveLength(0)
  })
})

describe('event listeners follow the gateway connection', () => {
  const getConnection = vi.fn()

  beforeEach(() => {
    vi.useFakeTimers()
    FakeSocket.all = []
    vi.stubGlobal('WebSocket', FakeSocket)
    $connection.set(null)
    getConnection.mockReset()
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { getConnection }
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
    $connection.set(null)
  })

  it('subscribes after a recovered first boot and re-subscribes on a new backend, one socket each', async () => {
    // First boot failed: no connection published, the backend is unreachable.
    getConnection.mockRejectedValue(new Error('Hermes backend did not become ready'))
    const onCron = vi.fn()
    const onReauth = vi.fn()

    const { rerender, unmount } = renderHook(({ cron, reauth }) => {
      useCronCompletionListener(cron)
      useProviderEventsListener(reauth)
    }, { initialProps: { cron: onCron, reauth: onReauth } })

    await flush()
    expect(FakeSocket.all).toHaveLength(0)
    expect(getConnection).not.toHaveBeenCalled()

    // gateway-recovery brings the backend back and publishes the connection.
    getConnection.mockResolvedValue(connection('http://127.0.0.1:9120'))
    act(() => $connection.set(connection('http://127.0.0.1:9120')))
    await flush()

    expect(FakeSocket.open().map(socket => new URL(socket.url).searchParams.get('channel')).sort()).toEqual([
      'cron_events',
      'provider_events'
    ])

    // Events reach the handlers.
    const cron = FakeSocket.open().find(socket => socket.url.includes('cron_events'))
    cron?.onmessage?.({ data: JSON.stringify({ job_id: 'j1', success: true, timestamp: 'now', type: 'cron_job_completed' }) })
    expect(onCron).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'j1' }))

    // Same backend re-published (window state, new handler identity): no new sockets.
    act(() => $connection.set({ ...connection('http://127.0.0.1:9120'), isFullscreen: true }))
    rerender({ cron: vi.fn(), reauth: vi.fn() })
    await flush()
    expect(FakeSocket.all).toHaveLength(2)

    // A recovery onto a new backend port: old sockets closed, new ones opened.
    getConnection.mockResolvedValue(connection('http://127.0.0.1:9333', 't1'))
    act(() => $connection.set(connection('http://127.0.0.1:9333', 't1')))
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS * 2)
    })

    expect(FakeSocket.open()).toHaveLength(2)
    expect(FakeSocket.open().every(socket => socket.url.startsWith('ws://127.0.0.1:9333/'))).toBe(true)
    expect(FakeSocket.all).toHaveLength(4)

    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS * 3)
    })
    expect(FakeSocket.open()).toHaveLength(0)
    expect(FakeSocket.all).toHaveLength(4)
  })

  it('keeps retrying while the published backend is not answering yet', async () => {
    getConnection.mockRejectedValueOnce(new Error('timeout')).mockResolvedValue(connection('http://127.0.0.1:9120'))
    act(() => $connection.set(connection('http://127.0.0.1:9120')))

    renderHook(() => useCronCompletionListener(vi.fn()))
    await flush()
    expect(FakeSocket.all).toHaveLength(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVENTS_CHANNEL_RETRY_MS)
    })
    expect(FakeSocket.open()).toHaveLength(1)
  })
})
