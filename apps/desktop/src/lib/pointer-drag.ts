import type { PointerEvent as ReactPointerEvent } from 'react'

// Controls keep their own pointer behaviour; a drag only starts on "chrome"
// (titles, headers, padding). `data-no-drag` opts any subtree out explicitly.
const DRAG_EXEMPT_SELECTOR =
  'a, button, input, select, textarea, label, summary, [contenteditable=""], [contenteditable="true"], [role="button"], [role="checkbox"], [role="combobox"], [role="link"], [role="menuitem"], [role="option"], [role="radio"], [role="slider"], [role="switch"], [role="tab"], [role="textbox"], [data-no-drag]'

export function isDragExempt(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(DRAG_EXEMPT_SELECTOR))
}

/** Track a primary-button drag from `event` until release.
 *
 *  `onMove` gets the distance from the start point. Uses pointer capture like
 *  the pane resize handles, and restores cursor + text selection afterwards. */
export function beginPointerDrag(
  event: ReactPointerEvent<HTMLElement>,
  {
    cursor = 'grabbing',
    onEnd,
    onMove
  }: { cursor?: string; onEnd?: () => void; onMove: (dx: number, dy: number) => void }
): boolean {
  if (event.button !== 0) {
    return false
  }

  event.preventDefault()

  const handle = event.currentTarget
  const pointerId = event.pointerId
  const startX = event.clientX
  const startY = event.clientY
  const previousCursor = document.body.style.cursor
  const previousUserSelect = document.body.style.userSelect

  handle.setPointerCapture?.(pointerId)
  document.body.style.cursor = cursor
  document.body.style.userSelect = 'none'

  const handleMove = (moveEvent: PointerEvent) => onMove(moveEvent.clientX - startX, moveEvent.clientY - startY)

  const finish = () => {
    window.removeEventListener('pointermove', handleMove)
    window.removeEventListener('pointerup', finish)
    window.removeEventListener('pointercancel', finish)
    handle.releasePointerCapture?.(pointerId)
    document.body.style.cursor = previousCursor
    document.body.style.userSelect = previousUserSelect
    onEnd?.()
  }

  window.addEventListener('pointermove', handleMove)
  window.addEventListener('pointerup', finish)
  window.addEventListener('pointercancel', finish)

  return true
}

/** Keep a dragged box reachable: its top edge stays on screen and at least
 *  `grip` px of it stay inside the viewport horizontally and vertically. */
export function clampToViewport(
  left: number,
  top: number,
  width: number,
  height: number,
  grip = 64
): { left: number; top: number } {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  return {
    left: Math.min(Math.max(left, grip - width), viewportWidth - grip),
    top: Math.min(Math.max(top, 0), Math.max(0, viewportHeight - Math.min(grip, height)))
  }
}
