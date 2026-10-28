import { useI18n } from '@/i18n'

/**
 * While the connection to the backend is down, a running turn keeps going
 * there but none of its progress reaches the chat. Say so instead of leaving
 * a frozen (or, after a reload, older) transcript that reads as lost (AIS-735).
 */
export function shouldShowReconnectingNotice(
  gatewayState: string | undefined,
  busy: boolean,
  hasSession: boolean
): boolean {
  return hasSession && busy && gatewayState !== 'open'
}

export function ReconnectingNotice({ visible }: { visible: boolean }) {
  const { t } = useI18n()

  if (!visible) {
    return null
  }

  const copy = t.assistant.thread

  return (
    <div
      aria-live="polite"
      className="mx-auto mt-2 flex w-full max-w-3xl items-start gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm"
      data-testid="reconnecting-notice"
      role="status"
    >
      <span
        aria-hidden="true"
        className="dither mt-1 inline-block size-3 shrink-0 rounded-[2px] text-midground/80 animate-pulse"
      />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{copy.reconnectingTitle}</div>
        <div className="mt-0.5 text-muted-foreground">{copy.reconnectingBody}</div>
      </div>
    </div>
  )
}
