import { contextTone, type ContextTone } from '@/lib/statusbar'
import { cn } from '@/lib/utils'

const RADIUS = 5
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

// Semantic status colours: calm = the accent, filling = amber, full = rose.
const TONE_CLASS: Record<ContextTone, string> = {
  calm: 'text-(--theme-primary)',
  filling: 'text-amber-500',
  full: 'text-rose-500'
}

/**
 * The co-worker status bar's context gauge (AIS-794): a small ring that fills
 * as the conversation uses up its space. Words live in the tooltip.
 */
export function ContextRing({ className, percent }: { className?: string; percent: number }) {
  const bounded = Math.max(0, Math.min(100, percent))

  return (
    <svg
      aria-hidden
      className={cn('size-3 shrink-0 -rotate-90', TONE_CLASS[contextTone(bounded)], className)}
      data-percent={bounded}
      data-testid="context-ring"
      viewBox="0 0 12 12"
    >
      <circle cx="6" cy="6" fill="none" r={RADIUS} stroke="currentColor" strokeOpacity={0.22} strokeWidth={1.75} />
      <circle
        cx="6"
        cy="6"
        fill="none"
        r={RADIUS}
        stroke="currentColor"
        strokeDasharray={CIRCUMFERENCE}
        strokeDashoffset={CIRCUMFERENCE * (1 - bounded / 100)}
        strokeLinecap="round"
        strokeWidth={1.75}
      />
    </svg>
  )
}
