import { useEffect, useRef } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'

interface SidebarLoadMoreRowProps {
  step: number
  onClick: () => void
  loading?: boolean
}

// "Load N more" affordance shared by the messaging, background and profile
// sections. The chevron sits in the same w-3.5 column the rows use for their
// dot, so it lines up with the list above.
export function SidebarLoadMoreRow({ step, onClick, loading = false }: SidebarLoadMoreRowProps) {
  const { t } = useI18n()
  const label = loading ? t.sidebar.loading : step > 0 ? t.sidebar.loadCount(step) : t.sidebar.loadMore

  return (
    <button
      className="flex min-h-5 items-center gap-1.5 self-start bg-transparent pl-2 text-left text-[0.6875rem] text-(--ui-text-tertiary) transition-colors duration-100 ease-out hover:text-foreground hover:transition-none disabled:cursor-default disabled:opacity-60 disabled:hover:text-(--ui-text-tertiary)"
      disabled={loading}
      onClick={onClick}
      type="button"
    >
      <span className="grid w-3.5 shrink-0 place-items-center">
        <Codicon className="opacity-70" name={loading ? 'loading' : 'chevron-down'} size="0.75rem" spinning={loading} />
      </span>
      <span>{label}</span>
    </button>
  )
}

interface SidebarLoadMoreSentinelProps {
  /** Rows loaded so far. A load is asked for once per count, so a page that
   *  brings nothing new cannot loop. */
  loaded: number
  loading: boolean
  onLoadMore: () => void
}

// AIS-794: the session list loads its next page when its end scrolls into
// view, instead of a "load more" row. Placed after the last row; shows a
// spinner while a page loads.
export function SidebarLoadMoreSentinel({ loaded, loading, onLoadMore }: SidebarLoadMoreSentinelProps) {
  const ref = useRef<HTMLDivElement>(null)
  const onLoadMoreRef = useRef(onLoadMore)
  const requestedAt = useRef<null | number>(null)

  onLoadMoreRef.current = onLoadMore

  useEffect(() => {
    const node = ref.current

    if (!node || loading || requestedAt.current === loaded || typeof IntersectionObserver === 'undefined') {
      return
    }

    // The implicit root still honours the scrolling ancestors' clipping, so
    // this fires only once the sentinel is really visible in the list.
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && requestedAt.current !== loaded) {
        requestedAt.current = loaded
        onLoadMoreRef.current()
      }
    })

    observer.observe(node)

    return () => observer.disconnect()
  }, [loaded, loading])

  return (
    <div aria-hidden className="flex min-h-5 shrink-0 items-center pl-2" data-testid="sidebar-load-more" ref={ref}>
      {loading && (
        <span className="grid w-3.5 place-items-center text-(--ui-text-tertiary)">
          <Codicon name="loading" size="0.75rem" spinning />
        </span>
      )}
    </div>
  )
}
