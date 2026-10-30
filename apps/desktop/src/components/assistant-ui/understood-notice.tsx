import { useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { type KeyboardEvent, useId, useState } from 'react'

import { useI18n } from '@/i18n'
import { ChevronDown } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $liveUnderstood, threadUserTurnKey, understoodKey } from '@/store/understood'

function contentText(content: unknown): string {
  if (typeof content === 'string') {
    return content.trim()
  }

  if (!Array.isArray(content)) {
    return ''
  }

  return content
    .map(part =>
      part && typeof part === 'object' && (part as { type?: unknown }).type === 'text'
        ? String((part as { text?: unknown }).text ?? '')
        : ''
    )
    .join('')
    .trim()
}

const isString = (value: unknown): value is string => typeof value === 'string'

interface UnderstoodNoticeProps {
  /** Index of the pinned turn's user message in the thread. */
  messageIndex: number
  /** Runtime session id, for lines that arrived live (store/understood.ts). */
  sessionId: null | string
}

/**
 * What the model understood (AIS-789), pinned above the viewport while the
 * reader is in a turn whose question has scrolled away. It replaces the old
 * sticky question bubble: one line, collapsed, with a chevron; opening it
 * shows the whole line and the original question. No line, no notice.
 *
 * Mount it with `key={messageIndex}` so a newly pinned turn starts collapsed.
 */
export function UnderstoodNotice({ messageIndex, sessionId }: UnderstoodNoticeProps) {
  const { t } = useI18n()
  const copy = t.assistant.thread.understood
  const message = useAuiState(s => s.thread.messages[messageIndex])
  const live = useStore($liveUnderstood)
  const [open, setOpen] = useState(false)
  const panelId = useId()

  if (!message || message.role !== 'user') {
    return null
  }

  const question = contentText(message.content)
  const custom = (message.metadata?.custom ?? {}) as { attachmentRefs?: unknown; understood?: unknown }
  const refs = Array.isArray(custom.attachmentRefs) ? custom.attachmentRefs.filter(isString) : []
  const stored = isString(custom.understood) ? custom.understood.trim() : ''
  const understood = stored || (sessionId ? live[understoodKey(sessionId, threadUserTurnKey(question, refs))] : '')

  if (!understood) {
    return null
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation()
      setOpen(false)
    }
  }

  return (
    // The fade behind keeps text that scrolls under the notice from showing
    // in the gap above it.
    <div className="flex w-full justify-center bg-linear-to-b from-(--ui-chat-surface-background) from-70% to-transparent pt-(--turn-notice-top) pb-2">
      <div
        className="pointer-events-auto w-(--thread-width) max-w-full overflow-hidden rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) shadow-xs [-webkit-app-region:no-drag]"
        data-open={open ? 'true' : undefined}
        data-slot="understood-notice"
        onKeyDown={onKeyDown}
      >
        <button
          aria-controls={open ? panelId : undefined}
          aria-expanded={open}
          className="flex w-full min-w-0 items-start gap-2 bg-transparent px-3 py-1 text-left text-[0.8125rem] leading-5"
          onClick={() => setOpen(value => !value)}
          title={open ? copy.collapse : copy.expand}
          type="button"
        >
          <span className="shrink-0 font-medium text-(--ui-text-tertiary)">{copy.label}</span>
          <span
            className={cn('min-w-0 flex-1 text-(--ui-text-secondary)', open ? 'whitespace-normal' : 'truncate')}
            data-slot="understood-notice-line"
          >
            {understood}
          </span>
          <ChevronDown
            aria-hidden
            className={cn(
              'mt-0.75 size-3.5 shrink-0 text-(--ui-text-tertiary) transition-transform duration-150',
              open && 'rotate-180'
            )}
          />
        </button>
        {open && (
          <div
            className="max-h-[40vh] overflow-y-auto border-t border-(--ui-stroke-tertiary) px-3 pt-1.5 pb-2"
            data-slot="understood-notice-question"
            id={panelId}
            role="region"
          >
            <div className="text-[0.75rem] font-medium text-(--ui-text-tertiary)">{copy.question}</div>
            <p className="m-0 whitespace-pre-wrap wrap-anywhere text-[length:var(--thread-text-font-size)] leading-(--dt-line-height) text-foreground/95">
              {question}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
