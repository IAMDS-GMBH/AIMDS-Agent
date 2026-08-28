import { ThreadPrimitive, useAuiEvent, useAuiState } from '@assistant-ui/react'
import { useVirtualizer, type Virtualizer } from '@tanstack/react-virtual'
import {
  type ComponentProps,
  type FC,
  memo,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef
} from 'react'

import { setMutableRef } from '@/lib/mutable-ref'
import { createSmoothScroller, type SmoothScrollController } from '@/lib/smooth-scroll'
import { cn } from '@/lib/utils'
import { setThreadScrolledUp } from '@/store/thread-scroll'

const ESTIMATED_ITEM_HEIGHT = 220
const OVERSCAN = 4
const AT_BOTTOM_THRESHOLD = 4

type ThreadMessageComponents = ComponentProps<typeof ThreadPrimitive.MessageByIndex>['components']

type MessageGroup = { id: string; index: number; kind: 'standalone' } | { id: string; indices: number[]; kind: 'turn' }

interface VirtualizedThreadProps {
  clampToComposer: boolean
  components: ThreadMessageComponents
  emptyPlaceholder?: ReactNode
  loadingIndicator?: ReactNode
  sessionKey?: string | null
}

function buildGroups(signature: string): MessageGroup[] {
  if (!signature) {
    return []
  }

  const messages = signature.split('\n').map(row => {
    const [index, id, role] = row.split(':')

    return { id, index: Number(index), role }
  })

  const groups: MessageGroup[] = []

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i]

    if (message.role !== 'user') {
      groups.push({ id: message.id, index: message.index, kind: 'standalone' })

      continue
    }

    const indices = [message.index]

    while (i + 1 < messages.length && messages[i + 1].role !== 'user') {
      indices.push(messages[++i].index)
    }

    groups.push({ id: message.id, indices, kind: 'turn' })
  }

  return groups
}

const VirtualizedThreadInner: FC<VirtualizedThreadProps> = ({
  clampToComposer,
  components,
  emptyPlaceholder,
  loadingIndicator,
  sessionKey
}) => {
  const messageSignature = useAuiState(s =>
    s.thread.messages
      .map((message, index) => {
        const rawParts = (message as { parts?: unknown }).parts
        const partsCount = Array.isArray(rawParts) ? rawParts.length : 0
        const statusType = (message as { status?: { type?: string } }).status?.type ?? ''
        const content = (message as { content?: unknown }).content

        const contentLength =
          typeof content === 'string'
            ? content.length
            : Array.isArray(content)
              ? content.length
              : 0

        return `${index}:${message.id}:${message.role}:${statusType}:${partsCount}:${contentLength}`
      })
      .join('\n')
  )

  const isRunning = useAuiState(s => s.thread.isRunning)

  const groups = useMemo(() => buildGroups(messageSignature), [messageSignature])
  const renderEmpty = groups.length === 0 && Boolean(emptyPlaceholder)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)

  // Shared ref so scrollToFn can check whether the user is parked at the
  // bottom without needing a ref from inside useThreadScrollAnchor.
  const stickyBottomRef = useRef(true)

  const virtualizer = useVirtualizer({
    count: groups.length,
    estimateSize: () => ESTIMATED_ITEM_HEIGHT,
    getItemKey: index => {
      const group = groups[index]

      if (!group) {return index}

      return group.kind === 'turn' ? `${group.id}:${group.indices.join('-')}` : `${group.id}:${group.index}`
    },
    getScrollElement: () => scrollerRef.current,
    // Seed the rect so the initial range mounts something before
    // `observeElementRect` reports the real layout (it overrides this).
    initialRect: { height: 600, width: 800 },
    overscan: OVERSCAN,
    // When the virtualizer adjusts scroll due to item measurement changes,
    // skip the adjustment if the user is at the bottom. Our ResizeObserver +
    // pinToBottom loop handles scroll anchoring; letting the virtualizer also
    // adjust creates a feedback loop where the two fight each other,
    // producing visible rubber-banding (the view snaps to the composer
    // then jumps back up).
    scrollToFn: (offset, _options, instance) => {
      const el = instance.scrollElement

      if (!el) {
        return
      }

      if (stickyBottomRef.current) {
        const maxScroll = el.scrollHeight - el.clientHeight
        const distFromBottom = maxScroll - el.scrollTop

        if (distFromBottom <= AT_BOTTOM_THRESHOLD && offset < maxScroll) {
          return
        }
      }

      ;(el as HTMLElement).scrollTo(0, offset)
    }
  })

  useThreadScrollAnchor({
    contentRef,
    enabled: !renderEmpty,
    groupCount: groups.length,
    isRunning,
    scrollerRef,
    sessionKey: sessionKey ?? null,
    stickyBottomRef,
    virtualizer
  })

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()
  const paddingTop = virtualItems[0]?.start ?? 0
  const paddingBottom = Math.max(0, totalSize - (virtualItems.at(-1)?.end ?? 0))

  return (
    <div
      className="relative min-h-0 max-w-full overflow-hidden contain-[layout_paint]"
      style={{ height: clampToComposer ? 'var(--thread-viewport-height)' : '100%' }}
    >
      <div
        className="size-full overflow-x-hidden overflow-y-auto overscroll-contain"
        data-slot="aui_thread-viewport"
        ref={scrollerRef}
      >
        {renderEmpty ? (
          <div
            className="mx-auto grid h-full w-full max-w-(--composer-width) grid-rows-[minmax(0,1fr)_auto] min-w-0 gap-(--conversation-turn-gap) px-6 py-8"
            data-slot="aui_thread-content"
          >
            {emptyPlaceholder}
          </div>
        ) : (
          <div
            className={cn(
              'mx-auto flex w-full max-w-(--composer-width) min-w-0 flex-col px-6 pt-[calc(var(--titlebar-height)+1.5rem)]'
            )}
            data-slot="aui_thread-content"
            ref={contentRef}
          >
            {/* Natural-flow virtualization: mounted items render as normal
                flex siblings so `position: sticky` on the human bubble
                resolves against the scroller without transform interference.
                Padding spacers reserve scroll space for unmounted items. */}
            <div style={{ paddingBottom: `${paddingBottom}px`, paddingTop: `${paddingTop}px` }}>
              {virtualItems.map(virtualItem => {
                const group = groups[virtualItem.index]

                if (!group) {
                  return null
                }

                return (
                  <div
                    className="flex min-w-0 flex-col gap-(--conversation-turn-gap) pb-(--conversation-turn-gap)"
                    data-index={virtualItem.index}
                    key={virtualItem.key}
                    ref={virtualizer.measureElement}
                  >
                    {group.kind === 'turn' ? (
                      <div
                        className="composer-human-ai-pair-container relative flex min-w-0 flex-col gap-(--conversation-turn-gap)"
                        data-slot="aui_turn-pair"
                      >
                        {group.indices.map(index => (
                          <ThreadPrimitive.MessageByIndex components={components} index={index} key={index} />
                        ))}
                      </div>
                    ) : (
                      <ThreadPrimitive.MessageByIndex components={components} index={group.index} />
                    )}
                  </div>
                )
              })}
            </div>
            {loadingIndicator}
            {clampToComposer && (
              <div
                aria-hidden="true"
                className="shrink-0"
                data-slot="aui_composer-clearance"
                style={{ height: 'var(--thread-last-message-clearance)' }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export const VirtualizedThread = memo(VirtualizedThreadInner)

interface ScrollAnchorOptions {
  contentRef: React.RefObject<HTMLDivElement | null>
  enabled: boolean
  groupCount: number
  isRunning: boolean
  scrollerRef: React.RefObject<HTMLDivElement | null>
  sessionKey: string | null
  stickyBottomRef: React.MutableRefObject<boolean>
  virtualizer: Virtualizer<HTMLDivElement, Element>
}

function useThreadScrollAnchor({
  contentRef,
  enabled,
  groupCount,
  isRunning,
  scrollerRef,
  sessionKey,
  stickyBottomRef,
  virtualizer
}: ScrollAnchorOptions) {
  // `stickyBottomRef` = parked at bottom, content growth should follow. Cleared on
  // user-driven upward scroll; re-armed when they reach bottom again.
  // This is a shared ref — scrollToFn reads it to prevent the virtualizer's
  // measurement adjustments from fighting our pinToBottom.
  const lastTopRef = useRef(0)
  const lastHeightRef = useRef(0)
  const lastClientHeightRef = useRef(0)
  // `pinToBottom` now animates scrollTop smoothly over multiple frames rather
  // than writing it once, so a single "was this our write" event-counter no
  // longer works — the animation itself (not one scroll event) is the unit
  // of "ours". `scrollController.isAnimating()` covers the whole glide; see
  // `apps/desktop/scripts/measure-jump.mjs` for the original repro this
  // guard exists to prevent (distFromBottom 0 → 49 within one frame, a
  // programmatic write misread as the user scrolling up, sticking forever).
  const scrollControllerRef = useRef<SmoothScrollController | null>(null)
  const scrollControllerElRef = useRef<HTMLDivElement | null>(null)
  const prevSessionKeyRef = useRef(sessionKey)
  const prevGroupCountRef = useRef(0)

  const getScrollController = useCallback((el: HTMLDivElement) => {
    if (scrollControllerElRef.current !== el) {
      scrollControllerRef.current?.cancel()
      scrollControllerRef.current = createSmoothScroller(el)
      scrollControllerElRef.current = el
    }

    return scrollControllerRef.current
  }, [])

  const pinToBottom = useCallback(() => {
    const el = scrollerRef.current

    if (!el) {
      return
    }

    // Already parked at the bottom: no animation needed, and starting one
    // for a no-op distance would just hold the "ours" guard open for no
    // reason. Repeated pins (streaming heartbeats, the post-run lock loop)
    // would otherwise re-trigger it constantly. Refresh trackers, bail.
    const distFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight)

    if (distFromBottom <= AT_BOTTOM_THRESHOLD) {
      lastTopRef.current = el.scrollTop
      lastHeightRef.current = el.scrollHeight
      lastClientHeightRef.current = el.clientHeight

      return
    }

    // Retargeting an in-flight animation (rather than restarting one) is
    // what makes repeated calls during streaming read as one continuous
    // glide instead of a stutter of tiny re-starts.
    getScrollController(el)?.scrollTo(el.scrollHeight - el.clientHeight)
    lastTopRef.current = el.scrollTop
    lastHeightRef.current = el.scrollHeight
    lastClientHeightRef.current = el.clientHeight
  }, [getScrollController, scrollerRef])

  const jumpToBottom = useCallback(() => {
    setMutableRef(stickyBottomRef, true)

    if (groupCount > 0) {
      virtualizer.scrollToIndex(groupCount - 1, { align: 'end', behavior: 'auto' })
    }

    requestAnimationFrame(() => {
      if (stickyBottomRef.current) {
        pinToBottom()
      }
    })
  }, [groupCount, pinToBottom, stickyBottomRef, virtualizer])

  useEffect(
    () => () => {
      setThreadScrolledUp(false)
      scrollControllerRef.current?.cancel()
    },
    []
  )

  // Track at-bottom state, dim composer when scrolled up, disarm on user
  // scroll/wheel/touch.
  useEffect(() => {
    const el = scrollerRef.current

    if (!el) {
      return undefined
    }

    const disarm = () => {
      setMutableRef(stickyBottomRef, false)
      // Real user intervention should stop the chase immediately rather
      // than letting an in-flight smooth-scroll finish fighting them.
      scrollControllerRef.current?.cancel()
    }

    const onScroll = () => {
      const top = el.scrollTop
      const controller = scrollControllerRef.current

      // If this scroll event is a consequence of `pinToBottom`'s smooth
      // animation writing `el.scrollTop` on each frame, treat the whole
      // animation window as ours: don't disarm, don't flicker the
      // scrolled-up affordance while we're still gliding toward bottom.
      // Without this guard, an in-progress frame's scrollTop gets misread
      // as the user scrolling up, disarming sticky-bottom permanently and
      // leaving the just-submitted message below the fold.
      //
      // A bare `isAnimating()` check isn't enough though: the user can also
      // grab the scrollbar or wheel mid-animation, which fires an ordinary
      // scroll event indistinguishable from our own frame write by type
      // alone. Comparing against `getExpectedTop()` (the value the
      // animation itself last wrote) tells the two apart — a real user
      // scroll lands somewhere the animation didn't just write, so it falls
      // through to the normal disarm logic below instead.
      if (controller?.isAnimating() && Math.abs(top - controller.getExpectedTop()) <= 1) {
        lastTopRef.current = top
        lastHeightRef.current = el.scrollHeight
        lastClientHeightRef.current = el.clientHeight
        // Always re-arm — sticky-bottom should hold through clamp races.
        setMutableRef(stickyBottomRef, true)
        setThreadScrolledUp(false)

        return
      }

      // A real scroll landed while our animation was still running: stop it
      // immediately so the next frame doesn't overwrite the user's position.
      controller?.cancel()

      // Disarm only when `scrollTop` decreases while both content height and
      // viewport height are stable. A bare `top < lastTopRef.current` check is
      // unsafe: virtualizer measurement, streaming markdown, composer resizing,
      // window resizing, and toolbar/status updates can all move scrollTop as a
      // layout side effect. Wheel-up and touchmove still disarm immediately via
      // their own listeners below, so real user intent remains covered.
      const heightGrew = el.scrollHeight > lastHeightRef.current
      const clientHeightChanged = Math.abs(el.clientHeight - lastClientHeightRef.current) > 1

      if (!heightGrew && !clientHeightChanged && top + 1 < lastTopRef.current) {
        setMutableRef(stickyBottomRef, false)
      }

      lastTopRef.current = top
      lastHeightRef.current = el.scrollHeight
      lastClientHeightRef.current = el.clientHeight

      const atBottom = el.scrollHeight - (top + el.clientHeight) <= AT_BOTTOM_THRESHOLD

      if (atBottom) {
        setMutableRef(stickyBottomRef, true)
      }

      setThreadScrolledUp(!atBottom)
    }

    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) {
        disarm()
      }
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchmove', disarm, { passive: true })

    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchmove', disarm)
    }
  }, [scrollerRef, stickyBottomRef])

  // Streaming auto-follow: while a turn is running and the user is parked at
  // the bottom (stickyBottomRef), re-pin on every content-size change so the
  // view tracks tokens as they stream in — the classic chat-follow behavior.
  // Gated on `isRunning` so this never fights the user once a turn settles;
  // `pinToBottom`'s own distFromBottom check + the shared `stickyBottomRef`
  // disarm-on-scroll-up logic above still apply, so a manual scroll-up
  // during streaming still releases the pin immediately.
  useEffect(() => {
    if (!enabled || !isRunning) {
      return undefined
    }

    const content = contentRef.current

    if (!content) {
      return undefined
    }

    const observer = new ResizeObserver(() => {
      if (stickyBottomRef.current) {
        pinToBottom()
      }
    })

    observer.observe(content)

    return () => observer.disconnect()
  }, [contentRef, enabled, isRunning, pinToBottom, stickyBottomRef])

  // Jump to bottom on session change OR when an empty thread first gets
  // content. Both share the same intent and the same effect.
  useEffect(() => {
    const sessionChanged = prevSessionKeyRef.current !== sessionKey
    const becameNonEmpty = prevGroupCountRef.current === 0 && groupCount > 0

    prevSessionKeyRef.current = sessionKey
    prevGroupCountRef.current = groupCount

    if (enabled && (sessionChanged || becameNonEmpty)) {
      jumpToBottom()
    }
  }, [enabled, groupCount, jumpToBottom, sessionKey])

  // Pre-paint pin: when groupCount increases while armed (a new turn arriving
  // from the user submit or assistant turn start), pin BEFORE the browser
  // commits the layout to screen. Using useLayoutEffect rather than useEffect
  // so this runs synchronously after React commits the DOM mutation but before
  // the browser paints. Without this, there's a ~50ms visual window where the
  // new message sits below the fold.
  //
  // We pin TWICE in this critical path — once synchronously, then once on
  // the next rAF. The second pin catches the case where React mounts the
  // new message in the second commit (after our layout effect ran), which
  // grows scrollHeight again; without the rAF pin the user briefly sees a
  // ~15 px gap below the new message. This fires once per user submit / new
  // turn arrival — it is NOT streaming-token follow (that path is removed
  // above), so a turn that streams a long response after this initial jump
  // will not chase the bottom.
  const prevGroupCountForLayoutRef = useRef(groupCount)
  useLayoutEffect(() => {
    if (!enabled) {
      return
    }

    if (groupCount > prevGroupCountForLayoutRef.current && stickyBottomRef.current) {
      // Defer to rAF so that browser scroll/wheel events from the current
      // frame are processed first.  Without this deferral, a trackpad
      // scroll-up during streaming can race with this effect: the wheel
      // event hasn't fired yet so stickyBottomRef is still true, and the
      // immediate pinToBottom() would snap the viewport back to bottom
      // against the user's intent.
      requestAnimationFrame(() => {
        if (stickyBottomRef.current) {
          pinToBottom()
        }
      })
    }

    prevGroupCountForLayoutRef.current = groupCount
  }, [enabled, groupCount, pinToBottom, stickyBottomRef])

  // Intentionally NO post-run bottom lock. Earlier builds kept pinning to
  // the bottom for POST_RUN_BOTTOM_LOCK_MS after `isRunning` flipped false to
  // chase final Shiki re-highlight measurement. With streaming follow gone,
  // re-pinning at completion would yank the viewport back to the bottom even
  // though the user is reading earlier content — the opposite of what's
  // wanted. The one-time submit / new-turn jump already covers landing a
  // fresh message in view.
  const prevIsRunningForLayoutRef = useRef(isRunning)
  useLayoutEffect(() => {
    prevIsRunningForLayoutRef.current = isRunning
  }, [isRunning])

  useAuiEvent('thread.runStart', jumpToBottom)
}
