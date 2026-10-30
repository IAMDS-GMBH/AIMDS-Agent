import { useEffect, useState } from 'react'

import type { UsageStats } from '@/types/hermes'

export function formatK(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0'
  }

  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`
  }

  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}k`
  }

  return `${Math.round(value)}`
}

export function formatDuration(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000))
  const seconds = totalSeconds % 60
  const minutes = Math.floor(totalSeconds / 60) % 60
  const hours = Math.floor(totalSeconds / 3600)
  const ss = String(seconds).padStart(2, '0')
  const mm = String(minutes).padStart(2, '0')

  return hours > 0 ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`
}

export function compactPath(path: string, max = 44): string {
  const trimmed = path.trim()

  if (trimmed.length <= max) {
    return trimmed
  }

  const segments = trimmed.split('/').filter(Boolean)

  if (segments.length < 2) {
    return `…${trimmed.slice(-(max - 1))}`
  }

  const tail = segments.slice(-2).join('/')

  return tail.length + 2 >= max ? `…${tail.slice(-(max - 1))}` : `…/${tail}`
}

export function contextBar(percent: number | undefined, width = 10): string {
  const bounded = Math.max(0, Math.min(100, percent ?? 0))
  const filled = Math.round((bounded / 100) * width)

  return `${'█'.repeat(filled)}${'░'.repeat(width - filled)}`
}

export function usageContextLabel(usage: UsageStats): string {
  if (usage.context_max) {
    return `${formatK(usage.context_used ?? 0)}/${formatK(usage.context_max)}`
  }

  return usage.total > 0 ? `${formatK(usage.total)} tok` : ''
}

export function contextBarLabel(usage: UsageStats): string {
  if (!usage.context_max) {
    return ''
  }

  const pct = Math.max(0, Math.min(100, Math.round(usage.context_percent ?? 0)))

  return `[${contextBar(usage.context_percent)}] ${pct}%`
}

// ─── Status bar posture (AIS-794) ───────────────────────────────────────────
// The desktop is the co-worker posture: by default the status bar shows a
// connection dot, the model in plain words and a small context ring. The
// technical items come back with Settings → "Technische Details" (developer
// view only). Items views add at runtime pass through either way.

/** Shown only with "Technische Details". */
export const TECHNICAL_STATUSBAR_ITEM_IDS: ReadonlySet<string> = new Set([
  'agents',
  'command-center',
  'context-usage',
  'cron',
  'gateway-health',
  'running-timer',
  'session-timer'
])

/** The co-worker stand-ins for the technical items. */
export const COWORKER_STATUSBAR_ITEM_IDS: ReadonlySet<string> = new Set(['connection', 'context-ring'])

export function statusbarItemsForPosture<T extends { id: string }>(items: readonly T[], technical: boolean): T[] {
  const hidden = technical ? COWORKER_STATUSBAR_ITEM_IDS : TECHNICAL_STATUSBAR_ITEM_IDS

  return items.filter(item => !hidden.has(item.id))
}

export type ConnectionState = 'connecting' | 'offline' | 'ready' | 'setup'

/** What the connection dot says, from the gateway state and the model check. */
export function connectionState(gatewayState: string, inference: { ready?: boolean } | null): ConnectionState {
  if (gatewayState === 'open') {
    return inference?.ready ? 'ready' : inference ? 'setup' : 'connecting'
  }

  return gatewayState === 'connecting' ? 'connecting' : 'offline'
}

/** How full the conversation is, 0–100, or null when the limit is unknown. */
export function contextPercent(usage: UsageStats): null | number {
  if (!usage.context_max) {
    return null
  }

  return Math.max(0, Math.min(100, Math.round(usage.context_percent ?? 0)))
}

export type ContextTone = 'calm' | 'full' | 'filling'

export function contextTone(percent: number): ContextTone {
  return percent >= 90 ? 'full' : percent >= 70 ? 'filling' : 'calm'
}

export function LiveDuration({ since }: { since: number | null | undefined }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!since) {
      return
    }

    const tick = () => setNow(Date.now())
    tick()
    const timer = window.setInterval(tick, 1000)

    return () => window.clearInterval(timer)
  }, [since])

  return since ? formatDuration(now - since) : null
}
