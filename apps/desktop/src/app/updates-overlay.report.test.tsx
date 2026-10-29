/**
 * AIS-755: the "Report Issue" buttons of the update dialog pre-fill the
 * report summary. It was German on every UI language ("Problem beim Update");
 * it follows the UI language now.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent } from '@/components/ui/dialog'
import { I18nProvider } from '@/i18n'
import { de } from '@/i18n/de'
import { en } from '@/i18n/en'

import type { DesktopUpdateStatus } from '../global'

import { IdleView } from './updates-overlay'

const available: DesktopUpdateStatus = {
  behind: 1,
  branch: 'stable',
  installedVersion: '0.7.13',
  source: 'release',
  supported: true,
  targetTag: 'v0.7.14',
  targetVersion: '0.7.14'
}

function renderIdleView(status: DesktopUpdateStatus | null, locale: 'de' | 'en', onReportIssue: (summary?: string) => void) {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <Dialog open>
        <DialogContent>
          <IdleView
            behind={status?.behind ?? 0}
            checking={false}
            commits={[]}
            onInstall={vi.fn()}
            onLater={vi.fn()}
            onReportIssue={onReportIssue}
            onRetryCheck={vi.fn()}
            status={status}
            target="client"
          />
        </DialogContent>
      </Dialog>
    </I18nProvider>
  )
}

describe('update report summaries (AIS-755)', () => {
  afterEach(cleanup)

  it.each([
    ['en', en],
    ['de', de]
  ] as const)('use the UI language (%s)', (locale, t) => {
    const cases: [DesktopUpdateStatus | null, string][] = [
      [null, t.updates.reportSummary.checkFailed],
      [{ ...available, error: 'fetch failed' } as DesktopUpdateStatus, t.updates.reportSummary.checkUnreachable],
      [available, t.updates.reportSummary.generic]
    ]

    for (const [status, summary] of cases) {
      const onReportIssue = vi.fn()
      renderIdleView(status, locale, onReportIssue)
      fireEvent.click(screen.getByRole('button', { name: t.reportIssue.title }))
      expect(onReportIssue).toHaveBeenCalledWith(summary)
      cleanup()
    }
  })

  it('has no German summary on the English UI', () => {
    const summaries = [...Object.values(en.updates.reportSummary), en.install.reportSummary]
      .map(value => (typeof value === 'function' ? value('x') : value))
      .join(' ')

    expect(summaries).not.toMatch(/Problem beim|fehlgeschlagen|Prüfung|Unbekannt|hängt/)
    expect(en.updates.reportSummary.failed(null)).toBe('Update failed: unknown error')
    expect(de.updates.reportSummary.failed('Timeout')).toBe('Update fehlgeschlagen: Timeout')
  })
})
