import { createContext, type ReactNode, type PointerEvent as ReactPointerEvent, useContext, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { translateNow } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { beginPointerDrag, clampToViewport, isDragExempt } from '@/lib/pointer-drag'
import { cn } from '@/lib/utils'
import {
  type FloatableOverlay,
  FLOATING_MIN_HEIGHT,
  FLOATING_MIN_WIDTH,
  type FloatingGeometry,
  loadFloatingGeometry,
  saveFloatingGeometry
} from '@/store/floating-overlay'

interface OverlayFloatingState {
  floating: boolean
  /** Detach into a floating window, or dock back to the full-screen overlay. */
  onToggleFloating: () => void
  view: FloatableOverlay
}

// Provided by the desktop controller around each detachable overlay, so the
// overlay views themselves need no extra props (AIS-398).
export const OverlayFloatingContext = createContext<OverlayFloatingState | null>(null)

interface OverlayViewProps {
  children: ReactNode
  onClose: () => void
  closeLabel?: string
  contentClassName?: string
  headerContent?: ReactNode
  rootClassName?: string
}

export function OverlayView({
  children,
  onClose,
  closeLabel = translateNow('common.close'),
  contentClassName,
  headerContent,
  rootClassName
}: OverlayViewProps) {
  const floatingState = useContext(OverlayFloatingContext)
  const floating = Boolean(floatingState?.floating)
  const cardRef = useRef<HTMLDivElement | null>(null)
  const [geometry, setGeometry] = useState<FloatingGeometry | null>(null)

  const floatingView = floatingState?.view

  useEffect(() => {
    setGeometry(floating && floatingView ? loadFloatingGeometry(floatingView) : null)
  }, [floating, floatingView])

  const closeOverlay = () => {
    triggerHaptic('close')
    onClose()
  }

  // Esc dismisses every OverlayView-based overlay. Nested Radix dialogs
  // stop propagation themselves, so opening (e.g.) the model picker inside
  // Settings still closes the picker first instead of the underlying overlay.
  // A floating window only reacts while focus is inside it: Esc in the chat
  // behind it belongs to the chat.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }

      if (floating && !cardRef.current?.contains(document.activeElement)) {
        return
      }

      event.preventDefault()
      triggerHaptic('close')
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [floating, onClose])

  const persist = (next: FloatingGeometry) => {
    if (floatingState) {
      saveFloatingGeometry(floatingState.view, next)
    }
  }

  const startMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!geometry || isDragExempt(event.target)) {
      return
    }

    const start = geometry
    let latest = start

    beginPointerDrag(event, {
      onEnd: () => persist(latest),
      onMove: (dx, dy) => {
        const { left, top } = clampToViewport(start.x + dx, start.y + dy, start.width, start.height)
        latest = { ...start, x: left, y: top }
        setGeometry(latest)
      }
    })
  }

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!geometry) {
      return
    }

    const start = geometry
    let latest = start

    beginPointerDrag(event, {
      cursor: 'nwse-resize',
      onEnd: () => persist(latest),
      onMove: (dx, dy) => {
        latest = {
          ...start,
          height: Math.min(Math.max(start.height + dy, FLOATING_MIN_HEIGHT), window.innerHeight - start.y),
          width: Math.min(Math.max(start.width + dx, FLOATING_MIN_WIDTH), window.innerWidth - start.x)
        }
        setGeometry(latest)
      }
    })
  }

  const showFloating = floating && geometry !== null

  return (
    <div
      className={cn(
        'fixed inset-0 z-50',
        showFloating ? 'pointer-events-none' : 'bg-black/22 p-3 backdrop-blur-[0.125rem] sm:p-6'
      )}
      data-floating={showFloating ? '' : undefined}
      onClick={event => {
        if (!floating && event.target === event.currentTarget) {
          closeOverlay()
        }
      }}
      role="presentation"
    >
      <div
        className={cn(
          'relative flex min-h-0 flex-col overflow-hidden rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background)',
          showFloating ? 'pointer-events-auto absolute shadow-nous' : 'h-full shadow-md',
          rootClassName
        )}
        ref={cardRef}
        style={
          showFloating && geometry
            ? { height: geometry.height, left: geometry.x, top: geometry.y, width: geometry.width }
            : undefined
        }
      >
        <div
          className={cn(
            'absolute inset-x-0 top-0 z-10 h-[calc(var(--titlebar-height)+0.1875rem)]',
            showFloating ? 'cursor-grab [-webkit-app-region:no-drag]' : 'pointer-events-none [-webkit-app-region:drag]'
          )}
          onPointerDown={showFloating ? startMove : undefined}
        >
          {headerContent && (
            <div className="pointer-events-auto absolute left-1/2 top-[calc(0.5rem+var(--titlebar-height)/2)] -translate-x-1/2 -translate-y-1/2 [-webkit-app-region:no-drag]">
              {headerContent}
            </div>
          )}

          {floatingState && (
            <Button
              aria-label={floating ? translateNow('common.dockWindow') : translateNow('common.floatWindow')}
              className="pointer-events-auto absolute right-12 top-[calc(0.1875rem+var(--titlebar-height)/2)] -translate-y-1/2 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground [-webkit-app-region:no-drag]"
              onClick={floatingState.onToggleFloating}
              size="icon-titlebar"
              title={floating ? translateNow('common.dockWindow') : translateNow('common.floatWindow')}
              variant="ghost"
            >
              <Codicon name={floating ? 'screen-full' : 'multiple-windows'} size="1rem" />
            </Button>
          )}

          <Button
            aria-label={closeLabel}
            className="pointer-events-auto absolute right-3 top-[calc(0.1875rem+var(--titlebar-height)/2)] -translate-y-1/2 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground [-webkit-app-region:no-drag]"
            onClick={closeOverlay}
            size="icon-titlebar"
            variant="ghost"
          >
            <Codicon name="close" size="1rem" />
          </Button>
        </div>

        {/* No top padding here: the split-layout columns own their own
            titlebar clearance so their backgrounds run flush to the card top
            (otherwise the card surface shows as a gap above the sidebar). */}
        <div className={cn('min-h-0 flex flex-1 flex-col', contentClassName)}>{children}</div>

        {showFloating && (
          <div
            aria-hidden
            className="absolute bottom-0 right-0 z-20 size-4 cursor-nwse-resize"
            data-no-drag
            onPointerDown={startResize}
          />
        )}
      </div>
    </div>
  )
}
