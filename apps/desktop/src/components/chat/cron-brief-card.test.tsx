import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { setCronJobs } from '@/store/cron'
import { $filePreviewTabs } from '@/store/preview'
import type { CronJob } from '@/types/hermes'

import { CronBriefCard, pickBriefJob } from './cron-brief-card'

function job(overrides: Partial<CronJob> = {}): CronJob {
  return { enabled: true, id: 'job-1', name: 'Morning brief', ...overrides }
}

function renderCard() {
  return render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <CronBriefCard />
      </I18nProvider>
    </MemoryRouter>
  )
}

describe('pickBriefJob', () => {
  const now = new Date('2026-09-08T09:00:00Z')

  it('prefers the newest unseen output of any job', () => {
    const unseen = [job({ id: 'u', name: 'Backup', last_output_at: '2026-09-08T06:00:00Z' })]

    expect(pickBriefJob([job({ id: 'b', last_output_at: '2026-09-08T07:00:00Z' }), ...unseen], unseen, now)?.id).toBe(
      'u'
    )
  })

  it("falls back to today's most recent brief job", () => {
    const jobs = [
      job({ id: 'old', last_output_at: '2026-09-07T06:00:00Z', last_seen_at: '2026-09-07T07:00:00Z' }),
      job({ id: 'today', last_output_at: '2026-09-08T06:00:00Z', last_seen_at: '2026-09-08T07:00:00Z' }),
      job({ id: 'other', name: 'Backup', last_output_at: '2026-09-08T08:00:00Z', last_seen_at: '2026-09-08T08:30:00Z' })
    ]

    expect(pickBriefJob(jobs, [], now)?.id).toBe('today')
    expect(pickBriefJob([jobs[0]], [], now)).toBeNull()
  })
})

describe('CronBriefCard', () => {
  let api: ReturnType<typeof vi.fn>
  let readFileText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn().mockResolvedValue({})
    readFileText = vi
      .fn()
      .mockResolvedValue({ path: '/tmp/brief.md', text: '# Brief\n\nFINDING: Parsed locally.\nNEXT: Do it.' })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        api,
        normalizePreviewTarget: vi.fn(async (target: string) => ({
          kind: 'file',
          label: 'brief.md',
          language: 'markdown',
          path: target,
          previewKind: 'text',
          source: target,
          url: `file://${target}`
        })),
        readFileText
      }
    })
    $filePreviewTabs.set([])
    setCronJobs([])
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
    $filePreviewTabs.set([])
    setCronJobs([])
  })

  it('renders nothing without a brief', () => {
    setCronJobs([job({ id: 'x', name: 'Backup' })])
    const { container } = renderCard()

    expect(container.querySelector('[data-slot="cron_brief_card"]')).toBeNull()
  })

  it('shows the backend summary, the New badge and opens the brief on click', async () => {
    setCronJobs([
      job({
        last_output_at: new Date().toISOString(),
        last_output_path: '/tmp/brief.md',
        last_output_summary: { finding: 'Two PRs wait.', next: 'Review #62.' },
        last_run_session_id: 'cron_job-1_1'
      })
    ])
    api.mockResolvedValue({ id: 'job-1', last_seen_at: new Date().toISOString() })
    renderCard()

    expect(screen.getByText('Morning brief')).toBeTruthy()
    expect(screen.getByText('Two PRs wait.')).toBeTruthy()
    expect(screen.getByText('Review #62.')).toBeTruthy()
    expect(screen.getByText('New')).toBeTruthy()
    expect(readFileText).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Open brief' }))

    await waitFor(() => expect($filePreviewTabs.get()).toHaveLength(1))
    expect($filePreviewTabs.get()[0].target.path).toBe('/tmp/brief.md')
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/cron/jobs/job-1/seen' }))
    )
    await waitFor(() => expect(screen.queryByText('New')).toBeNull())
  })

  it('parses FINDING/NEXT from the artifact when the backend summary is empty', async () => {
    setCronJobs([job({ last_output_at: new Date().toISOString(), last_output_path: '/tmp/brief.md' })])
    renderCard()

    await waitFor(() => expect(screen.getByText('Parsed locally.')).toBeTruthy())
    expect(screen.getByText('Do it.')).toBeTruthy()
    expect(readFileText).toHaveBeenCalledWith('/tmp/brief.md')
  })

  it('offers the run link only when a run session is known', () => {
    setCronJobs([job({ last_output_at: new Date().toISOString(), last_output_path: '/tmp/brief.md' })])
    renderCard()

    expect(screen.queryByRole('button', { name: 'Open run' })).toBeNull()
  })
})
