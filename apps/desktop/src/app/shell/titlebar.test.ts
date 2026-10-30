import { describe, expect, it } from 'vitest'

import {
  nativeControlsBand,
  nativeControlsWidth,
  TITLEBAR_CONTROL_OFFSET_X,
  TITLEBAR_EDGE_INSET,
  TITLEBAR_FALLBACK_WINDOW_BUTTON_X,
  TITLEBAR_HEIGHT,
  titlebarControlsPosition
} from './titlebar'

describe('titlebarControlsPosition', () => {
  it('offsets controls from visible traffic lights', () => {
    expect(titlebarControlsPosition({ x: 24, y: 10 }).left).toBe(24 + TITLEBAR_CONTROL_OFFSET_X)
  })

  it('pins to the edge when macOS fullscreen hides traffic lights', () => {
    expect(titlebarControlsPosition({ x: 24, y: 10 }, true).left).toBe(TITLEBAR_EDGE_INSET)
  })

  it('pins to the edge on Windows/Linux where native controls render on the right', () => {
    expect(titlebarControlsPosition(null).left).toBe(TITLEBAR_EDGE_INSET)
  })

  it('uses the macOS fallback while the initial window state is unknown', () => {
    expect(titlebarControlsPosition(undefined).left).toBe(TITLEBAR_FALLBACK_WINDOW_BUTTON_X + TITLEBAR_CONTROL_OFFSET_X)
  })
})

// AIS-798: one layout on every OS — the toolbar clears the native controls on
// whichever side the OS puts them.
describe('nativeControlsWidth', () => {
  it('is zero on macOS, whose traffic lights sit on the left', () => {
    expect(nativeControlsWidth(0, { width: 900, x: 80 }, 1000)).toBe(0)
  })

  it('uses the measured Windows/Linux controls width from the overlay rect', () => {
    // Windows: three 46px caption buttons.
    expect(nativeControlsWidth(144, { width: 862, x: 0 }, 1000)).toBe(138)
    // A Linux window manager with narrower buttons.
    expect(nativeControlsWidth(144, { width: 904, x: 0 }, 1000)).toBe(96)
  })

  it('falls back to the reported width without a usable rect', () => {
    expect(nativeControlsWidth(144, null, 1000)).toBe(144)
    expect(nativeControlsWidth(144, { width: 0, x: 0 }, 1000)).toBe(144)
    expect(nativeControlsWidth(144, { width: 1000, x: 0 }, 1000)).toBe(144)
    expect(nativeControlsWidth(144, { width: 100, x: 0 }, 1000)).toBe(144)
  })
})

describe('nativeControlsBand', () => {
  it('reserves the title bar height only where controls sit on the right', () => {
    expect(nativeControlsBand(138)).toBe(TITLEBAR_HEIGHT)
    expect(nativeControlsBand(0)).toBe(0)
  })
})
