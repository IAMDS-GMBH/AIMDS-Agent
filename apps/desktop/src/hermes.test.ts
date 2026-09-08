import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getCronJobLatestOutput, getCronJobRuns, listAllProfileSessions, listSessions, markCronJobSeen } from './hermes'

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

  it('marks a cron job seen with the profile query when known', async () => {
    api.mockResolvedValueOnce({ id: 'job 1', last_seen_at: '2026-09-08T06:00:00Z' })

    const job = await markCronJobSeen('job 1', 'work')

    expect(job.last_seen_at).toBe('2026-09-08T06:00:00Z')
    expect(api).toHaveBeenCalledWith({ method: 'POST', path: '/api/cron/jobs/job%201/seen?profile=work' })

    api.mockResolvedValueOnce({ id: 'job-2' })
    await markCronJobSeen('job-2')
    expect(api).toHaveBeenLastCalledWith({ method: 'POST', path: '/api/cron/jobs/job-2/seen' })
  })

  it('fetches the latest cron output', async () => {
    api.mockResolvedValueOnce({ content: '# hi', path: '/tmp/a.md', written_at: null })

    const out = await getCronJobLatestOutput('job-1', ' ')

    expect(out.content).toBe('# hi')
    expect(api).toHaveBeenCalledWith({ path: '/api/cron/jobs/job-1/output/latest' })
  })

  it('unwraps the {runs} envelope of the cron runs endpoint', async () => {
    api.mockResolvedValueOnce({ runs: [{ id: 'cron_job-1_1', output_path: '/tmp/a.md' }] })

    const runs = await getCronJobRuns('job-1', 1)

    expect(runs).toEqual([{ id: 'cron_job-1_1', output_path: '/tmp/a.md' }])
    expect(api).toHaveBeenCalledWith({ path: '/api/cron/jobs/job-1/runs?limit=1' })

    api.mockResolvedValueOnce({})
    expect(await getCronJobRuns('job-1')).toEqual([])
  })
})
