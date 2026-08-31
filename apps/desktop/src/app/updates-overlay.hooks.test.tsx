/**
 * AIS-276 regression: a mounted IdleView must survive every status transition
 * without changing its hook count. A second useI18n() below the early
 * returns rendered 2 hooks on the changelog path and 1 on every status path —
 * transitions on a mounted instance crashed the root boundary with React
 * #300 ("Rendered fewer hooks than expected") whenever an update finished,
 * a check failed mid-update, or the overlay target flipped.
 */
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent } from '@/components/ui/dialog'

import type { DesktopUpdateStatus } from '../global'

import { IdleView } from './updates-overlay'

const changelogStatus: DesktopUpdateStatus = {
  behind: 3,
  commits: [
    { at: 1756600000, author: 'dev', sha: 'abc1234', summary: 'feat: something' },
    { at: 1756500000, author: 'dev', sha: 'def5678', summary: 'fix: other' }
  ],
  supported: true
}

function renderIdleView(status: DesktopUpdateStatus | null, checking = false, behind = status?.behind ?? 0) {
  // IdleView renders DialogTitle internally — mirror the overlay's wrapper.
  return (
    <Dialog open>
      <DialogContent>
        <IdleView
          behind={behind}
          checking={checking}
          commits={status?.commits ?? []}
          onInstall={vi.fn()}
          onLater={vi.fn()}
          onReportIssue={vi.fn()}
          onRetryCheck={vi.fn()}
          status={status}
          target="client"
        />
      </DialogContent>
    </Dialog>
  )
}

describe('IdleView hook stability (AIS-276)', () => {
  it('survives changelog → error-status transitions on a mounted instance', () => {
    const errors: unknown[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...args) => errors.push(args))

    try {
      const { rerender } = render(renderIdleView(changelogStatus))

      // check failed mid-update
      rerender(renderIdleView({ error: 'check-failed', supported: true }, false, 0))
      // back to changelog
      rerender(renderIdleView(changelogStatus))
      // update finished: behind drops to 0
      rerender(renderIdleView({ behind: 0, supported: true }, false, 0))
      // unsupported backend target
      rerender(renderIdleView({ message: 'pip install', supported: false }, false, 0))
      // status gone while re-checking
      rerender(renderIdleView(null, true))

      const rendered = errors.flat().map(String).join('\n')

      expect(rendered).not.toMatch(/Rendered (fewer|more) hooks/i)
      expect(rendered).not.toMatch(/error #3[01]0/i)
    } finally {
      spy.mockRestore()
    }
  })
})
