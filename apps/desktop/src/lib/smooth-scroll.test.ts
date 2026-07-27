import { describe, expect, it } from 'vitest'

import { createSmoothScroller } from './smooth-scroll'

// A tiny fake rAF scheduler so the animation can be driven frame-by-frame
// deterministically instead of relying on real timers/jsdom's rAF shim.
function createFakeScheduler(startTime = 0) {
  let time = startTime
  let pending: FrameRequestCallback | null = null

  return {
    requestFrame: (cb: FrameRequestCallback) => {
      pending = cb

      return 1
    },
    cancelFrame: () => {
      pending = null
    },
    now: () => time,
    // Advances the fake clock and runs the pending frame callback, if any.
    tick(ms: number) {
      time += ms
      const cb = pending

      pending = null
      cb?.(time)
    },
    hasPendingFrame: () => pending != null
  }
}

describe('createSmoothScroller', () => {
  it('animates scrollTop from the current position to the target over the given duration', () => {
    const scheduler = createFakeScheduler()
    const el = { scrollTop: 0 }
    const scroller = createSmoothScroller(el, {
      durationMs: 100,
      requestFrame: scheduler.requestFrame,
      cancelFrame: scheduler.cancelFrame,
      now: scheduler.now
    })

    scroller.scrollTo(100)
    expect(scroller.isAnimating()).toBe(true)

    scheduler.tick(50)
    // Ease-out-cubic at t=0.5 is 1 - 0.5^3 = 0.875, not linear 0.5.
    expect(el.scrollTop).toBeCloseTo(87.5, 1)
    expect(scroller.isAnimating()).toBe(true)

    scheduler.tick(50)
    expect(el.scrollTop).toBe(100)
    expect(scroller.isAnimating()).toBe(false)
    expect(scheduler.hasPendingFrame()).toBe(false)
  })

  it('retargets an in-flight animation instead of restarting from the original start position', () => {
    const scheduler = createFakeScheduler()
    const el = { scrollTop: 0 }
    const scroller = createSmoothScroller(el, {
      durationMs: 100,
      requestFrame: scheduler.requestFrame,
      cancelFrame: scheduler.cancelFrame,
      now: scheduler.now
    })

    scroller.scrollTo(100)
    scheduler.tick(50)
    const midpoint = el.scrollTop

    expect(midpoint).toBeGreaterThan(0)
    expect(midpoint).toBeLessThan(100)

    // Content grew again mid-glide: retarget further down. The animation
    // should continue smoothly from `midpoint`, not jump or restart from 0.
    scroller.scrollTo(200)
    expect(el.scrollTop).toBe(midpoint)

    scheduler.tick(100)
    expect(el.scrollTop).toBe(200)
    expect(scroller.isAnimating()).toBe(false)
  })

  it('cancel() stops the animation without further writes', () => {
    const scheduler = createFakeScheduler()
    const el = { scrollTop: 0 }
    const scroller = createSmoothScroller(el, {
      durationMs: 100,
      requestFrame: scheduler.requestFrame,
      cancelFrame: scheduler.cancelFrame,
      now: scheduler.now
    })

    scroller.scrollTo(100)
    scheduler.tick(50)
    const midpoint = el.scrollTop

    scroller.cancel()
    expect(scroller.isAnimating()).toBe(false)
    expect(scheduler.hasPendingFrame()).toBe(false)

    scheduler.tick(50)
    expect(el.scrollTop).toBe(midpoint)
  })

  it('jumps immediately when durationMs is 0', () => {
    const scheduler = createFakeScheduler()
    const el = { scrollTop: 0 }
    const scroller = createSmoothScroller(el, {
      durationMs: 0,
      requestFrame: scheduler.requestFrame,
      cancelFrame: scheduler.cancelFrame,
      now: scheduler.now
    })

    scroller.scrollTo(50)
    scheduler.tick(0)
    expect(el.scrollTop).toBe(50)
    expect(scroller.isAnimating()).toBe(false)
  })
})
