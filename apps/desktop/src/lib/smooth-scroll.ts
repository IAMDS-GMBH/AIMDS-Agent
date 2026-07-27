// Small, dependency-free smooth-scroll primitive for the chat thread
// viewport. Extracted from thread-virtualizer.tsx so the interpolation math
// can be unit-tested without a real DOM/ResizeObserver.
//
// Design constraints (see thread-virtualizer.tsx's useThreadScrollAnchor for
// the full history): the scroll-anchor guard logic needs to reliably tell
// "a scroll event we caused" apart from "the user scrolled". An instant
// `el.scrollTop = x` write fires exactly one scroll event, which the old
// code tracked with a counter. A smooth animation instead writes scrollTop
// on every animation frame, so callers must treat the *entire* animation
// window (not a single event) as "ours" — see `isAnimating()` below.

export const DEFAULT_SMOOTH_SCROLL_DURATION_MS = 220

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3
}

export interface SmoothScrollController {
  /** Animate (or retarget an in-flight animation) toward `target` scrollTop. */
  scrollTo: (target: number) => void
  /** Stop any in-flight animation without changing scrollTop further. */
  cancel: () => void
  /** True while a `scrollTo`-driven animation is still writing scrollTop. */
  isAnimating: () => boolean
  /**
   * The scrollTop value this controller itself last wrote (or the position
   * it started from, before the first frame runs). Callers use this to tell
   * a `scroll` event caused by the controller's own frame write apart from
   * one caused by real, external user input (mouse drag on the scrollbar,
   * OS momentum scroll, etc.) arriving mid-animation — a bare `isAnimating()`
   * check can't distinguish the two, since both produce ordinary `scroll`
   * events on the element.
   */
  getExpectedTop: () => number
}

interface SmoothScrollOptions {
  durationMs?: number
  /** Injected for tests; defaults to the real rAF/cancel pair. */
  requestFrame?: (cb: FrameRequestCallback) => number
  cancelFrame?: (handle: number) => void
  now?: () => number
}

export function createSmoothScroller(
  el: { scrollTop: number },
  options: SmoothScrollOptions = {}
): SmoothScrollController {
  const durationMs = options.durationMs ?? DEFAULT_SMOOTH_SCROLL_DURATION_MS
  const requestFrame = options.requestFrame ?? requestAnimationFrame
  const cancelFrame = options.cancelFrame ?? cancelAnimationFrame
  const now = options.now ?? (() => performance.now())

  let frameHandle: number | null = null
  let startTop = 0
  let startTime = 0
  let target = 0
  let animating = false
  let expectedTop = el.scrollTop

  const step = () => {
    const elapsed = now() - startTime
    const t = durationMs <= 0 ? 1 : Math.min(1, elapsed / durationMs)

    expectedTop = startTop + (target - startTop) * easeOutCubic(t)
    el.scrollTop = expectedTop

    if (t >= 1) {
      animating = false
      frameHandle = null

      return
    }

    frameHandle = requestFrame(step)
  }

  return {
    scrollTo(newTarget: number) {
      // Retarget in place: continue from wherever the viewport actually is
      // right now (not the previous target), so rapid successive calls
      // during streaming chase the bottom smoothly instead of restarting
      // from a stale start point or stacking animations.
      startTop = el.scrollTop
      startTime = now()
      target = newTarget
      animating = true
      expectedTop = startTop

      if (frameHandle == null) {
        frameHandle = requestFrame(step)
      }
    },
    cancel() {
      if (frameHandle != null) {
        cancelFrame(frameHandle)
        frameHandle = null
      }

      animating = false
    },
    isAnimating: () => animating,
    getExpectedTop: () => expectedTop
  }
}
