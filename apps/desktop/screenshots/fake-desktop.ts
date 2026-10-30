/**
 * A fake desktop for the screenshot check (AIS-798): stands in for the
 * Electron preload bridge (`window.hermesDesktop`) and the backend's JSON-RPC
 * WebSocket, answering from deterministic fixtures. No backend, no network.
 *
 * `installFakeDesktop` runs in the page via `page.addInitScript`, so it must
 * stay self-contained — no imports, no closures over module scope.
 */

export interface FakeDesktopFixtures {
  /**
   * REST answers by path. A key may add query parameters
   * (`/api/profiles/sessions?source=cron`), which must all match; a key path
   * ending in `/*` answers every path below it. The most specific key wins;
   * unknown paths answer `{}`.
   */
  api: Record<string, unknown>
  /** JSON-RPC results by method; unknown methods answer `{}`. */
  rpc: Record<string, unknown>
}

export function installFakeDesktop(fixtures: FakeDesktopFixtures) {
  const calls: string[] = []

  ;(window as unknown as { __fakeDesktopCalls: string[] }).__fakeDesktopCalls = calls

  // Deterministic randomness. A seeded sequence is not enough: boot
  // animations draw a varying number of values first, so a component that
  // draws once at mount (tip of the day, composer placeholder, loader phase)
  // got a different value per run. Every draw is 0.5 plus a vanishing step —
  // the same pick for any `floor(random() * n)`, still distinct values. Ids
  // built from Math.random (Date.now() is frozen as well) are not unique; a
  // view that creates several needs its own handling.
  let draws = 0

  Math.random = () => 0.5 + (draws++ % 1_000_000) * 1e-12

  const clone = <T>(value: T): T => (value === undefined ? value : JSON.parse(JSON.stringify(value)))

  const apiAnswer = (path: string) => {
    const [bare, query = ''] = path.split('?')
    const params = new URLSearchParams(query)
    let best: { key: string; score: number } | null = null

    for (const key of Object.keys(fixtures.api)) {
      const [keyPath, keyQuery = ''] = key.split('?')
      const keyParams = [...new URLSearchParams(keyQuery)]
      const wildcard = keyPath.endsWith('/*')
      const pathMatches = wildcard ? bare.startsWith(keyPath.slice(0, -1)) : bare === keyPath

      if (!pathMatches || !keyParams.every(([name, value]) => params.get(name) === value)) {
        continue
      }

      // Exact path beats wildcard; more matched parameters beat fewer.
      const score = (wildcard ? 0 : 1_000_000) + keyParams.length * 1_000 + keyPath.length

      if (!best || score > best.score) {
        best = { key, score }
      }
    }

    return best === null ? {} : clone(fixtures.api[best.key])
  }

  const unsubscribe = () => () => {}

  function resolved<T>(value: T) {
    return () => Promise.resolve(value)
  }

  const connection = {
    baseUrl: 'http://127.0.0.1:9120',
    isFullscreen: false,
    logs: [],
    mode: 'local',
    nativeOverlayWidth: 0,
    source: 'local',
    token: 'screenshot',
    windowButtonPosition: { x: 24, y: 10 },
    wsUrl: 'ws://127.0.0.1:9120/api/ws?token=screenshot'
  }

  const bridge: Record<string, unknown> = {
    api: (request: { method?: string; path: string }) => {
      calls.push(`api ${request.method ?? 'GET'} ${request.path}`)

      return Promise.resolve(apiAnswer(request.path))
    },
    getBootProgress: resolved({
      error: null,
      fakeMode: true,
      message: '',
      phase: 'ready',
      progress: 1,
      running: false,
      timestamp: 0
    }),
    getBootstrapState: resolved({
      active: false,
      completedAt: 0,
      error: null,
      log: [],
      manifest: null,
      stages: {},
      startedAt: null,
      unsupportedPlatform: null
    }),
    getConnection: () => Promise.resolve(clone(connection)),
    getGatewayWsUrl: () => Promise.resolve(connection.wsUrl),
    getVersion: resolved({ appVersion: '0.7.15', electron: '44.5.1', platform: 'screenshot' }),
    readDir: () => Promise.resolve({ entries: [] }),
    sanitizeWorkspaceCwd: (cwd?: null | string) => Promise.resolve({ cwd: cwd || '/home/hermes', sanitized: false }),
    profile: { get: resolved({ profile: null }), set: resolved({ profile: null }) },
    settings: {
      getDefaultProjectDir: resolved({ defaultLabel: '~', dir: null, resolvedCwd: '/home/hermes' }),
      pickDefaultProjectDir: resolved({ canceled: true, dir: null }),
      setDefaultProjectDir: resolved({ dir: null })
    },
    terminal: {
      dispose: resolved(true),
      onData: unsubscribe,
      onExit: unsubscribe,
      resize: resolved(true),
      start: resolved({ id: 'screenshot', shell: 'zsh' }),
      write: resolved(true)
    },
    themes: { fetchMarketplace: resolved({ files: [] }), searchMarketplace: resolved([]) },
    uninstall: { run: resolved({ ok: false }), summary: resolved({}) },
    updates: {
      apply: resolved({ ok: false }),
      check: resolved({ available: false, behind: 0, commits: [] }),
      getBranch: resolved({ branch: 'stable', source: 'release' }),
      onProgress: unsubscribe,
      setBranch: resolved({ branch: 'stable', source: 'release' })
    }
  }

  // Anything else: `on*` subscriptions return an unsubscribe, other calls
  // resolve to null — and are recorded, so a new bridge call shows up in
  // SCREENSHOTS_DEBUG output instead of failing silently.
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = new Proxy(bridge, {
    get(target, prop) {
      if (typeof prop !== 'string') {
        return undefined
      }

      if (prop in target) {
        return target[prop]
      }

      calls.push(`bridge ${prop}`)

      return prop.startsWith('on') ? unsubscribe : () => Promise.resolve(null)
    }
  })

  // The backend gateway: a WebSocket that opens at once and answers every
  // JSON-RPC request from `fixtures.rpc`.
  class FakeGatewaySocket extends EventTarget {
    static readonly CONNECTING = 0
    static readonly OPEN = 1
    static readonly CLOSING = 2
    static readonly CLOSED = 3
    readonly CONNECTING = 0
    readonly OPEN = 1
    readonly CLOSING = 2
    readonly CLOSED = 3
    binaryType = 'blob'
    bufferedAmount = 0
    extensions = ''
    protocol = ''
    readyState = 0
    readonly url: string
    onclose: ((event: Event) => void) | null = null
    onerror: ((event: Event) => void) | null = null
    onmessage: ((event: MessageEvent) => void) | null = null
    onopen: ((event: Event) => void) | null = null

    constructor(url: string | URL) {
      super()
      this.url = String(url)
      setTimeout(() => {
        this.readyState = 1
        this.emit(new Event('open'))
      }, 0)
    }

    send(data: string) {
      let frame: { id?: number | string; method?: string }

      try {
        frame = JSON.parse(String(data))
      } catch {
        return
      }

      if (frame.id === undefined || !frame.method) {
        return
      }

      calls.push(`rpc ${frame.method}`)
      const result = frame.method in fixtures.rpc ? clone(fixtures.rpc[frame.method]) : {}
      const reply = JSON.stringify({ id: frame.id, jsonrpc: '2.0', result })
      setTimeout(() => this.emit(new MessageEvent('message', { data: reply })), 0)
    }

    close() {
      if (this.readyState === 3) {
        return
      }

      this.readyState = 3
      this.emit(new CloseEvent('close', { code: 1000 }))
    }

    private emit(event: Event) {
      this.dispatchEvent(event)
      const handler = (this as unknown as Record<string, unknown>)[`on${event.type}`]

      if (typeof handler === 'function') {
        handler.call(this, event)
      }
    }
  }

  ;(window as unknown as { WebSocket: unknown }).WebSocket = FakeGatewaySocket
}
