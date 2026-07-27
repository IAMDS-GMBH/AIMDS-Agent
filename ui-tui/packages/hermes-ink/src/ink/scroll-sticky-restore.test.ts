import { EventEmitter } from 'events'

import React from 'react'
import { describe, expect, it } from 'vitest'

import Box from './components/Box.js'
import ScrollBox, { type ScrollBoxHandle } from './components/ScrollBox.js'
import Text from './components/Text.js'
import Ink from './ink.js'

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 20
  rows = 5
  isTTY = true

  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()

    return true
  }
}

// Regression coverage for the auto-follow-to-bottom "jumps back" bug: a
// manual scroll that lands back at the true bottom (wheel tremor,
// click-select at max) transiently breaks stickyScroll, then the renderer
// positionally restores it on the very next pass. Before this fix,
// getLastManualScrollAt() kept reporting that restore-triggering scroll as
// "recent" for a full 1200ms afterward (useVirtualHistory.ts's recentManual
// gate reads it), which could make the virtualizer keep computing its mount
// window from the stale pre-restore scroll position and skip mounting a
// newly-arrived tail message — so the sticky-follow snap landed short of the
// real bottom instead of following it. This test asserts the timestamp is
// cleared the moment stickyScroll is positionally restored, closing that
// window immediately instead of leaving it open for up to 1200ms.
describe('ScrollBox sticky restore', () => {
  it('clears the manual-scroll timestamp when stickyScroll is positionally restored', () => {
    const stdout = new FakeTty()
    const stdin = new FakeTty()
    const stderr = new FakeTty()

    const ink = new Ink({
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream
    })

    const handleRef = React.createRef<ScrollBoxHandle>()
    const lines = Array.from({ length: 20 }, (_, i) => i)

    const tree = React.createElement(
      Box,
      { flexDirection: 'column', height: 5 },
      React.createElement(
        ScrollBox,
        { flexGrow: 1, ref: handleRef, stickyScroll: true },
        React.createElement(
          Box,
          { flexDirection: 'column' },
          lines.map(i => React.createElement(Text, { key: i }, `line ${i}`))
        )
      )
    )

    ink.render(tree)
    ink.onRender()

    const handle = handleRef.current

    if (!handle) {
      throw new Error('ScrollBox ref was not attached')
    }

    // Sanity: sticky by default, pinned to the true bottom.
    expect(handle.isSticky()).toBe(true)
    expect(handle.getLastManualScrollAt()).toBe(0)

    const maxScrollBeforeManualScroll = handle.getScrollTop()

    // A manual scroll that doesn't actually move the view (already at max)
    // — e.g. a wheel tremor or click-select at the bottom edge.
    handle.scrollBy(1)

    expect(handle.isSticky()).toBe(false)
    expect(handle.getLastManualScrollAt()).toBeGreaterThan(0)

    // The very next render pass positionally detects we're still at the
    // (unchanged) bottom and restores stickyScroll.
    ink.onRender()

    expect(handle.isSticky()).toBe(true)
    expect(handle.getScrollTop()).toBe(maxScrollBeforeManualScroll)
    // The fix under test: the manual-scroll timestamp must be cleared in
    // the same pass, not left "recent" for up to 1200ms.
    expect(handle.getLastManualScrollAt()).toBe(0)

    ink.unmount()
  })
})
