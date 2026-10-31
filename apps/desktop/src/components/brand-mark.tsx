import { useId } from 'react'

import { cn } from '@/lib/utils'

// Brand badge (AIS-794): an "A" monogram on the AIMDS Suite brand gradient
// (`--brand-gradient-from` → `--brand-gradient-to`), a softly rounded tile
// (radius a quarter of the side, 9px at the default 36px). Product-neutral on
// purpose — no product name or wordmark — so it survives the 0.8.0 rename.
// The same in light and dark. Size via className (default size-9 = 36px); the
// SVG scales with it.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  // Unique per instance: two marks on one page must not share a gradient id.
  const gradientId = `brand-mark-${useId().replace(/:/g, '')}`

  return (
    <span className={cn('inline-flex size-9 shrink-0', className)} data-testid="brand-mark" {...props}>
      <svg aria-hidden className="size-full" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient gradientUnits="userSpaceOnUse" id={gradientId} x1="0" x2="36" y1="0" y2="36">
            <stop offset="0" style={{ stopColor: 'var(--brand-gradient-from)' }} />
            <stop offset="1" style={{ stopColor: 'var(--brand-gradient-to)' }} />
          </linearGradient>
        </defs>
        <rect fill={`url(#${gradientId})`} height="36" rx="9" width="36" />
        {/* Geometric "A": two strokes and a crossbar, counter cut out. */}
        <path
          d="M15.6 8.5h4.8L28 27.5h-4.6l-1.6-4.3h-7.6l-1.6 4.3H8zM18 13.6l-2.4 6.3h4.8z"
          fill="#FFFFFF"
          fillRule="evenodd"
        />
      </svg>
    </span>
  )
}
