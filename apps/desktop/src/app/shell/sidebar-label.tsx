import type * as React from 'react'

import { cn } from '@/lib/utils'

// Section label of a side panel. AIS-794: the calm AIMDS Suite look — small,
// muted, letter-spaced caps; no brand colour or dither glyph competing with
// the content below.
export function SidebarPanelLabel({ children, className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      className={cn(
        'flex min-w-0 items-center pl-2 text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)',
        className
      )}
      {...props}
    >
      <span className="min-w-0 truncate leading-none">{children}</span>
    </span>
  )
}
