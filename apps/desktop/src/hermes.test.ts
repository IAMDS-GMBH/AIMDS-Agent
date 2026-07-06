import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { listAllProfileSessions, listSessions } from './hermes'

const emptySessionsResponse = {
  limit: 0,
  offset: 0,
  sessions: [],
  total: 0
}

describe('Hermes REST session helpers', () => {
  let api: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn().mockResolvedValue(emptySessionsResponse)
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('uses a longer timeout for the single-profile session list', async () => {
    await listSessions(50, 1)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent',
        timeoutMs: 60_000
      })
    )
  })

  it('uses a longer timeout for the all-profile session list', async () => {
    await listAllProfileSessions(50, 1)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/profiles/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent&profile=all',
        timeoutMs: 60_000
      })
    )
  })

  it('filters malformed session rows without ids in single-profile list', async () => {
    api.mockResolvedValueOnce({
      limit: 10,
      offset: 0,
      total: 3,
      sessions: [
        { id: '', title: 'Untitled Session' },
        { id: '   ', title: 'Whitespace id' },
        { id: 'sess-1', title: 'Valid session' }
      ]
    })

    const result = await listSessions(10, 1)

    expect(result.sessions.map(session => session.id)).toEqual(['sess-1'])
  })

  it('filters malformed session rows without ids in all-profile list', async () => {
    api.mockResolvedValueOnce({
      limit: 10,
      offset: 0,
      total: 3,
      sessions: [
        { id: null, title: 'Null id' },
        { id: undefined, title: 'Undefined id' },
        { id: 'sess-2', title: 'Valid session' }
      ]
    })

    const result = await listAllProfileSessions(10, 1)

    expect(result.sessions.map(session => session.id)).toEqual(['sess-2'])
  })
})
