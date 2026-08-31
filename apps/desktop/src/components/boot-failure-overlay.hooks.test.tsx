/**
 * AIS-276 regression: BootFailureOverlay must keep a stable hook count when
 * boot.error flips. A useState below the visibility early-return rendered 10
 * hooks visible / 9 hidden — every post-update boot failure crashed the ROOT
 * error boundary with React #310/#300 and replaced the recovery UI (retry /
 * open logs / send support logs) with the generic crash screen. Verified in
 * production logs: 8 paired occurrences, all right after update handoffs.
 */
import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { $desktopBoot } from '@/store/boot'

import { BootFailureOverlay } from './boot-failure-overlay'

describe('BootFailureOverlay hook stability (AIS-276)', () => {
  it('survives boot.error flipping on a mounted instance', () => {
    const errors: unknown[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...args) => errors.push(args))
    const initial = $desktopBoot.get()

    try {
      render(<BootFailureOverlay />)

      // Backend dies after the update handoff → overlay becomes visible.
      act(() => {
        $desktopBoot.set({ ...initial, error: 'Hermes backend exited before it became ready (1).', running: false })
      })

      // Backend recovers (or a retry succeeds) → overlay hides again.
      act(() => {
        $desktopBoot.set({ ...initial, error: null, running: true })
      })

      // And fails once more — the repeat case from the field reports.
      act(() => {
        $desktopBoot.set({ ...initial, error: 'connect ECONNREFUSED 127.0.0.1:9120', running: false })
      })

      const rendered = errors.flat().map(String).join('\n')

      expect(rendered).not.toMatch(/Rendered (fewer|more) hooks/i)
      expect(rendered).not.toMatch(/error #3[01]0/i)
    } finally {
      act(() => {
        $desktopBoot.set(initial)
      })
      spy.mockRestore()
    }
  })
})
