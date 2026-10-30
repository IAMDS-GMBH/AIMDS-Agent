// Scroll decisions of the chat thread (AIS-789), kept pure so they can be
// tested without a DOM. thread-virtualizer.tsx wires them to the scroller.

/** Within this many px of the end the thread counts as parked at the bottom. */
export const AT_BOTTOM_THRESHOLD = 4

interface ScrollBox {
  clientHeight: number
  scrollHeight: number
  scrollTop: number
}

/**
 * Where the virtualizer may move the viewport, or null to leave it alone.
 *
 * `@tanstack/react-virtual` keeps the content in place when a turn above the
 * viewport is measured at a new height: it asks to scroll to the current
 * offset *plus* the accumulated size delta (`adjustments`). Dropping that
 * delta made every re-measure above the viewport (code highlighting, images,
 * a tool block settling) shift the text under the reader — the jumps.
 *
 * While parked at the bottom the follow loop owns the position, so a
 * correction that would pull the view up off the bottom is skipped.
 */
export function threadScrollTarget(
  offset: number,
  adjustments: number | undefined,
  box: ScrollBox,
  parkedAtBottom: boolean
): null | number {
  const target = offset + (adjustments ?? 0)

  if (parkedAtBottom) {
    const maxScroll = box.scrollHeight - box.clientHeight

    if (maxScroll - box.scrollTop <= AT_BOTTOM_THRESHOLD && target < maxScroll) {
      return null
    }
  }

  return target
}

const SCROLL_UP_KEYS = new Set(['ArrowUp', 'Home', 'PageUp'])

/**
 * A key press that scrolls the thread up. Typing never counts: keys aimed at
 * an input, a text area or an editable element move a caret, not the view.
 */
export function isScrollUpKey(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'target'>): boolean {
  const target = event.target as HTMLElement | null

  if (target?.closest?.('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) {
    return false
  }

  return SCROLL_UP_KEYS.has(event.key) || (event.key === ' ' && event.shiftKey)
}
