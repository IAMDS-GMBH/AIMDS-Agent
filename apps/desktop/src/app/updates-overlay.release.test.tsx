/**
 * AIS-312: a release-managed install (archive updates, `.hermes-release.json`
 * marker) reports `source: 'release'` and a whole release version instead of
 * a commit log.
 *
 * AIS-750: every install gets the same end-user header — logo, product name,
 * target version ("Version 0.7.13 – Vorabversion") — and the curated release
 * notes in the UI language. No "Branch: preview · +12" chip, no commit
 * subjects with PR numbers on release channels.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent } from '@/components/ui/dialog'
import { I18nProvider } from '@/i18n'
import { PRODUCT_DISPLAY_NAME } from '@/lib/product'

import type { DesktopReleaseNote, DesktopUpdateStatus } from '../global'

import { IdleView } from './updates-overlay'

const releaseStatus: DesktopUpdateStatus = {
  behind: 1,
  branch: 'stable',
  commits: [],
  currentBranch: 'HEAD',
  currentSha: 'a'.repeat(40),
  headTag: 'v0.7.5',
  installedVersion: '0.7.5',
  releaseBuildId: '2026-09-01T10:00:00+00:00',
  releaseVersion: '0.7.5',
  source: 'release',
  supported: true,
  targetSha: 'c'.repeat(40),
  targetTag: 'v0.7.6',
  targetVersion: '0.7.6'
}

const notes: DesktopReleaseNote[] = [
  { de: 'Kanäle in Microsoft Teams lesen und dorthin posten', en: 'Read Microsoft Teams channels and post to them', group: 'new' },
  { de: 'Wartende Freigaben stehen jetzt sichtbar am Ende der Antwort', en: 'Pending approvals now show at the end of the answer', group: 'fixed' },
  { de: 'Nach einem Neustart über Nacht verbindet sich die App wieder von selbst', en: 'After a restart overnight the app reconnects on its own', group: 'fixed' }
]

// The v0.7.13-rc.1 dialog: preview channel, git install, raw squash subjects.
const previewStatus: DesktopUpdateStatus = {
  behind: 12,
  branch: 'preview',
  commits: [
    { at: 1761600000, author: 'dev', sha: 'a1', summary: 'feat(AIS-711): adopt OpenProject MCP 0.6 — one call per set, no per-project loops (#233)' },
    { at: 1761500000, author: 'dev', sha: 'a2', summary: 'fix(AIS-728): test runs no longer write into the real ~/.hermes/logs (#232)', testsOnly: true }
  ],
  installedVersion: '0.7.12',
  releaseNotes: notes,
  source: 'git',
  supported: true,
  targetTag: 'v0.7.13-rc.3',
  targetVersion: '0.7.13-rc.3'
}

function renderIdleView(status: DesktopUpdateStatus | null, { locale = 'en', onInstall = vi.fn() }: { locale?: 'de' | 'en'; onInstall?: () => void } = {}) {
  // IdleView renders DialogTitle internally — mirror the overlay's wrapper.
  return (
    <I18nProvider configClient={null} initialLocale={locale}>
      <Dialog open>
        <DialogContent>
          <IdleView
            behind={status?.behind ?? 0}
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

function listItems() {
  return Array.from(document.querySelectorAll('ul li')).map(li => li.textContent)
}

describe('IdleView release-managed install (AIS-312)', () => {
  // No vitest globals here, so testing-library does not auto-cleanup between tests.
  afterEach(cleanup)

  it('offers the release version with the product header and a neutral line without notes', () => {
    const onInstall = vi.fn()
    render(renderIdleView(releaseStatus, { onInstall }))

    expect(screen.getByText(PRODUCT_DISPLAY_NAME)).toBeTruthy()
    expect(screen.getByText('Version 0.7.6')).toBeTruthy()
    expect(screen.getByText('Installed: Version 0.7.5')).toBeTruthy()
    expect(screen.getByText('New update available')).toBeTruthy()
    expect(screen.queryByText(/Channel:|Branch:/)).toBeNull()
    expect(screen.queryByText(/\+1 Commit/i)).toBeNull()
    expect(listItems()).toEqual(['Fixes and improvements'])

    screen.getByRole('button', { name: 'Update now' }).click()
    expect(onInstall).toHaveBeenCalledTimes(1)
  })

  it('lists the manifest notes of a release install', () => {
    render(renderIdleView({ ...releaseStatus, releaseNotes: notes }))

    expect(listItems()).toEqual([
      'Read Microsoft Teams channels and post to them',
      'Pending approvals now show at the end of the answer',
      'After a restart overnight the app reconnects on its own'
    ])
  })

  it('stays on the up-to-date view when the release matches the marker', () => {
    render(renderIdleView({ ...releaseStatus, behind: 0, targetTag: 'v0.7.5', targetVersion: '0.7.5' }))

    expect(screen.queryByText('Version 0.7.5')).toBeNull()
    expect(screen.getByText('You’re all set')).toBeTruthy()
  })
})

describe('IdleView end-user release notes (AIS-750)', () => {
  afterEach(cleanup)

  it('renders the preview update in German: version line, curated notes, no developer text', () => {
    render(renderIdleView(previewStatus, { locale: 'de' }))

    expect(screen.getByText(PRODUCT_DISPLAY_NAME)).toBeTruthy()
    expect(screen.getByText('Version 0.7.13 – Vorabversion')).toBeTruthy()
    expect(screen.getByText('Installiert: Version 0.7.12')).toBeTruthy()
    expect(screen.getByText('Neues Update verfügbar')).toBeTruthy()
    expect(screen.getByText('Neuerungen')).toBeTruthy()
    expect(screen.getByText('Fehlerbehebungen')).toBeTruthy()
    expect(listItems()).toEqual([
      'Kanäle in Microsoft Teams lesen und dorthin posten',
      'Wartende Freigaben stehen jetzt sichtbar am Ende der Antwort',
      'Nach einem Neustart über Nacht verbindet sich die App wieder von selbst'
    ])
    expect(screen.getByRole('button', { name: 'Jetzt aktualisieren' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Problem melden' })).toBeTruthy()

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/Branch|#\d|AIS-|rc\.3|OpenProject MCP|Änderungen/)
    // one language only
    expect(text).not.toMatch(/Read Microsoft Teams|Update now/)
  })

  it('renders the same update in English', () => {
    render(renderIdleView(previewStatus, { locale: 'en' }))

    expect(screen.getByText('Version 0.7.13 – preview')).toBeTruthy()
    expect(listItems()[0]).toBe('Read Microsoft Teams channels and post to them')
    expect(screen.getByRole('button', { name: 'Report Issue' })).toBeTruthy()
    expect(document.body.textContent ?? '').not.toMatch(/Kanäle|Jetzt aktualisieren/)
  })

  it('keeps the scrubbed commit list for the main-channel developer view', () => {
    render(renderIdleView({ ...previewStatus, branch: 'main', releaseNotes: [], targetTag: undefined, targetVersion: undefined }, { locale: 'de' }))

    expect(screen.getByText('Entwicklungsstand · +12 Änderungen')).toBeTruthy()
    expect(listItems()).toEqual(['Adopt OpenProject MCP 0.6'])
    expect(screen.getByText('+ 11 weitere Änderungen enthalten.')).toBeTruthy()
  })

  // AIS-794: the brand mark is the product-neutral "A" monogram on the brand
  // gradient, drawn whole (no cropped bitmap, no product wordmark).
  it('shows the A monogram on the brand gradient as the brand mark', () => {
    render(renderIdleView(previewStatus))

    const mark = screen.getByTestId('brand-mark')
    const gradient = mark.querySelector('linearGradient')
    const tile = mark.querySelector('rect')

    expect(mark.querySelector('img')).toBeNull()
    expect(gradient?.querySelector('stop')?.getAttribute('style')).toContain('--brand-gradient-from')
    expect(tile?.getAttribute('fill')).toBe(`url(#${gradient?.id})`)
    expect(tile?.getAttribute('rx')).toBe('9')
  })
})
