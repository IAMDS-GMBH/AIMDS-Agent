import { describe, expect, it } from 'vitest'

import { isScrollUpKey, pinnedTurnIndex, threadScrollTarget } from './thread-follow'

const parkedBox = { clientHeight: 200, scrollHeight: 1000, scrollTop: 800 }
const readingBox = { clientHeight: 200, scrollHeight: 1000, scrollTop: 300 }

describe('threadScrollTarget (AIS-789)', () => {
  it('keeps the text in place while scrolled up: offset plus the measured size delta', () => {
    // A turn above the viewport grew by 120px: the view must move with it.
    expect(threadScrollTarget(300, 120, readingBox, false)).toBe(420)
    expect(threadScrollTarget(300, undefined, readingBox, false)).toBe(300)
  })

  it('leaves the position to the follow loop while parked at the bottom', () => {
    expect(threadScrollTarget(800, -40, parkedBox, true)).toBeNull()
  })

  it('still applies a correction that keeps a parked view at the bottom', () => {
    expect(threadScrollTarget(800, 0, parkedBox, true)).toBe(800)
  })

  it('applies corrections when the pin is armed but the view is not at the bottom', () => {
    expect(threadScrollTarget(300, 50, readingBox, true)).toBe(350)
  })
})

describe('isScrollUpKey', () => {
  const target = (tag: string, attrs: Record<string, string> = {}) => {
    const el = document.createElement(tag)

    for (const [name, value] of Object.entries(attrs)) {
      el.setAttribute(name, value)
    }

    return el
  }

  it('counts keys that scroll the thread up', () => {
    for (const key of ['ArrowUp', 'PageUp', 'Home']) {
      expect(isScrollUpKey({ key, shiftKey: false, target: target('div') })).toBe(true)
    }

    expect(isScrollUpKey({ key: ' ', shiftKey: true, target: target('div') })).toBe(true)
  })

  it('ignores other keys and anything typed into a field', () => {
    expect(isScrollUpKey({ key: 'ArrowDown', shiftKey: false, target: target('div') })).toBe(false)
    expect(isScrollUpKey({ key: ' ', shiftKey: false, target: target('div') })).toBe(false)
    expect(isScrollUpKey({ key: 'ArrowUp', shiftKey: false, target: target('textarea') })).toBe(false)
    expect(isScrollUpKey({ key: 'Home', shiftKey: false, target: target('div', { contenteditable: 'true' }) })).toBe(
      false
    )
  })
})

describe('pinnedTurnIndex (AIS-789)', () => {
  const turns = [
    { bottom: 100, questionBottom: -300, top: -400, userIndex: 0 },
    { bottom: 900, questionBottom: 160, top: 100, userIndex: 2 }
  ]

  it('pins the turn the reader is in once its question has scrolled away', () => {
    expect(pinnedTurnIndex(turns, 50)).toBe(0)
  })

  it('pins nothing while the question is still in view', () => {
    expect(pinnedTurnIndex(turns, 120)).toBeNull()
    expect(pinnedTurnIndex(turns, 160)).toBe(2)
  })

  it('pins nothing when no turn spans the top edge', () => {
    expect(pinnedTurnIndex(turns, 950)).toBeNull()
    expect(pinnedTurnIndex([], 0)).toBeNull()
  })
})
