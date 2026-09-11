/**
 * AIS-312: a release-managed install (archive updates, `.hermes-release.json`
 * marker) reports `source: 'release'` and a whole release version instead of
 * a commit log. The overlay must offer the version — and must not render the
 * (empty) commit changelog or its "+N commits" pill.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent } from '@/components/ui/dialog'
import { I18nProvider } from '@/i18n'

import type { DesktopUpdateStatus } from '../global'

import { IdleView } from './updates-overlay'

const releaseStatus: DesktopUpdateStatus = {
  behind: 1,
  branch: 'stable',
  commits: [],
  currentBranch: 'HEAD',
  currentSha: 'a'.repeat(40),
  headTag: 'v0.7.5',
  releaseBuildId: '2026-09-01T10:00:00+00:00',
  releaseVersion: '0.7.5',
  source: 'release',
  supported: true,
  targetSha: 'c'.repeat(40),
  targetTag: 'v0.7.6',
  targetVersion: '0.7.6'
}

function renderIdleView(status: DesktopUpdateStatus | null, onInstall = vi.fn(), behind = status?.behind ?? 0) {
  // IdleView renders DialogTitle internally — mirror the overlay's wrapper.
  // The default locale is German; pin English so the copy assertions read
  // like the i18n catalogue.
  return (
    <I18nProvider configClient={null} initialLocale="en">
      <Dialog open>
        <DialogContent>
          <IdleView
            behind={behind}
            checking={false}
            commits={status?.commits ?? []}
            onInstall={onInstall}
            onLater={vi.fn()}
            onReportIssue={vi.fn()}
            onRetryCheck={vi.fn()}
            status={status}
            target="client"
          />
        </DialogContent>
      </Dialog>
    </I18nProvider>
  )
}

describe('IdleView release-managed install (AIS-312)', () => {
  // No vitest globals here, so testing-library does not auto-cleanup between tests.
  afterEach(cleanup)

  it('offers the release version without a commit list', () => {
    const onInstall = vi.fn()
    render(renderIdleView(releaseStatus, onInstall))

    expect(screen.getByText('Version 0.7.6 available')).toBeTruthy()
    expect(screen.getByText('v0.7.6')).toBeTruthy()
    expect(screen.getByText('Channel: stable')).toBeTruthy()
    expect(screen.queryByText(/\+1 Commit/)).toBeNull()
    expect(screen.queryByText(/release notes/i)).toBeNull()
    expect(document.querySelectorAll('ul li').length).toBe(0)

    screen.getByRole('button', { name: 'Update now' }).click()
    expect(onInstall).toHaveBeenCalledTimes(1)
  })

  it('stays on the up-to-date view when the release matches the marker', () => {
    render(renderIdleView({ ...releaseStatus, behind: 0, targetTag: 'v0.7.5', targetVersion: '0.7.5' }))

    expect(screen.queryByText(/Version .* available/)).toBeNull()
    expect(screen.getByText('You’re all set')).toBeTruthy()
  })

  it('keeps the commit changelog for git installs', () => {
    render(
      renderIdleView({
        behind: 2,
        commits: [
          { at: 1756600000, author: 'dev', sha: 'abc1234', summary: 'feat: something' },
          { at: 1756500000, author: 'dev', sha: 'def5678', summary: 'fix: other' }
        ],
        source: 'git',
        supported: true
      })
    )

    expect(screen.queryByText(/Version .* available/)).toBeNull()
    expect(screen.getByText('New update available')).toBeTruthy()
    expect(document.querySelectorAll('ul li').length).toBeGreaterThan(0)
  })
})
