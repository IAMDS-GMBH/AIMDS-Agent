'use client'

import { type ToolCallMessagePartProps } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ToolFallback } from '@/components/assistant-ui/tool-fallback'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Check, Clock, HelpCircle, Loader2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $clarifyRequest, clearClarifyRequest } from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'

interface ClarifyArgs {
  question?: string
  choices?: string[] | null
}

function readClarifyArgs(args: unknown): ClarifyArgs {
  if (!args || typeof args !== 'object') {
    return {}
  }

  const row = args as Record<string, unknown>
  const choices = Array.isArray(row.choices) ? row.choices.filter((c): c is string => typeof c === 'string') : null

  return {
    question: typeof row.question === 'string' ? row.question : undefined,
    choices: choices && choices.length > 0 ? choices : null
  }
}

// Choice and "Other" rows share a layout; only color/hover differs.
const OPTION_ROW_CLASS = 'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors'

function RadioDot({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'grid size-3.5 shrink-0 place-items-center rounded-full border transition-colors',
        selected ? 'border-primary' : 'border-muted-foreground/40'
      )}
    >
      {selected && <span className="size-1.5 rounded-full bg-primary" />}
    </span>
  )
}

// AIS-333: the clarify tool result once the gateway stopped waiting. Any other
// settled result (answered, skipped, delivery error) renders through ToolFallback.
interface ClarifyTimeoutResult {
  timeoutSeconds: number | null
  question: string
}

export function readClarifyTimeout(result: unknown): ClarifyTimeoutResult | null {
  let row: Record<string, unknown> | null = null

  if (result && typeof result === 'object') {
    row = result as Record<string, unknown>
  } else if (typeof result === 'string' && result.trim().startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(result)
      row = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
    } catch {
      row = null
    }
  }

  if (!row) {
    return null
  }

  const state = typeof row.response_state === 'string' ? row.response_state.toLowerCase() : ''
  const reason = typeof row.reason_code === 'string' ? row.reason_code.toLowerCase() : ''

  if (state !== 'timeout' && reason !== 'clarify_timeout') {
    return null
  }

  const seconds = typeof row.timeout_seconds === 'number' && row.timeout_seconds > 0 ? row.timeout_seconds : null

  return { timeoutSeconds: seconds, question: typeof row.question === 'string' ? row.question : '' }
}

export function formatCountdown(remainingMs: number): string {
  const total = Math.max(0, Math.ceil(remainingMs / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60

  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

const COUNTDOWN_URGENT_MS = 30_000

export const ClarifyTool = (props: ToolCallMessagePartProps) => {
  const isPending = props.result === undefined

  // Once Hermes records an answer, fall back to the standard tool block so
  // the past Q/A renders consistently with every other tool in the thread.
  if (!isPending) {
    const timedOut = readClarifyTimeout(props.result)

    // AIS-333: an expired question used to collapse into the generic tool
    // block — the user saw it "disappear" (SUP-20260914-092956). Keep it as
    // a visible card that says what happened.
    if (timedOut) {
      return <ClarifyToolTimedOut question={timedOut.question || readClarifyArgs(props.args).question || ''} timeoutSeconds={timedOut.timeoutSeconds} />
    }

    return <ToolFallback {...props} />
  }

  return <ClarifyToolPending {...props} />
}

function ClarifyToolTimedOut({ question, timeoutSeconds }: { question: string; timeoutSeconds: number | null }) {
  const { t } = useI18n()
  const copy = t.assistant.clarify

  return (
    <div
      className="relative mb-3 mt-2 grid gap-2 rounded-[0.5rem] border border-dashed border-border/70 bg-card/30 px-3 py-2.5 text-sm"
      data-slot="clarify-timed-out"
      role="status"
    >
      <div className="flex items-start gap-2.5">
        <span
          aria-hidden
          className="mt-px grid size-6 shrink-0 place-items-center rounded-md bg-accent/60 text-muted-foreground ring-1 ring-inset ring-border/60"
        >
          <Clock className="size-3.5" />
        </span>
        <div className="flex-1 space-y-1">
          <div className="font-medium leading-snug text-foreground">{copy.timedOutTitle}</div>
          <p className="text-[0.8125rem] leading-snug text-muted-foreground">{copy.timedOutBody(timeoutSeconds ?? 0)}</p>
          {question && (
            <p className="whitespace-pre-wrap text-[0.8125rem] leading-snug text-foreground/85">
              <span className="text-muted-foreground">{copy.timedOutQuestion}: </span>
              {question}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// Ticks once a second while a deadline is known; null when there is none.
function useCountdown(deadlineAt: number | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!deadlineAt) {
      return undefined
    }

    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)

    return () => window.clearInterval(timer)
  }, [deadlineAt])

  if (!deadlineAt) {
    return null
  }

  return Math.max(0, deadlineAt - now)
}

function ClarifyToolPending({ args }: ToolCallMessagePartProps) {
  const { t } = useI18n()
  const copy = t.assistant.clarify
  const request = useStore($clarifyRequest)
  const gateway = useStore($gateway)
  const fromArgs = useMemo(() => readClarifyArgs(args), [args])

  const matchingRequest = useMemo(() => {
    if (!request) {
      return null
    }

    if (fromArgs.question && request.question && fromArgs.question !== request.question) {
      return null
    }

    return request
  }, [fromArgs.question, request])

  const question = fromArgs.question || matchingRequest?.question || ''

  const choices = useMemo(
    () => fromArgs.choices ?? matchingRequest?.choices ?? [],
    [fromArgs.choices, matchingRequest?.choices]
  )

  const hasChoices = choices.length > 0

  const [typing, setTyping] = useState(false)
  const [draft, setDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const isSubmittingRef = useRef(false)
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  // Race: tool.start fires a tick before clarify.request, so request_id
  // arrives slightly after the tool block mounts. Show the question (from
  // args) but disable submit until we have the request id from the gateway.
  const ready = Boolean(matchingRequest?.requestId)

  // AIS-333: visible deadline. The gateway stops waiting at deadlineAt and the
  // agent then continues on its own — the user needs to see that clock.
  const remainingMs = useCountdown(matchingRequest?.deadlineAt)

  const respond = useCallback(
    async (answer: string) => {
      if (isSubmittingRef.current || submitting) {
        return
      }

      if (!ready || !matchingRequest) {
        notifyError(new Error(copy.notReady), copy.sendFailed)

        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed)

        return
      }

      isSubmittingRef.current = true
      setSubmitting(true)

      try {
        await gateway.request<{ ok?: boolean }>('clarify.respond', {
          request_id: matchingRequest.requestId,
          answer
        })
        triggerHaptic('submit')
        clearClarifyRequest(matchingRequest.requestId, matchingRequest.sessionId)
        // The matching tool.complete will land shortly after, swapping this
        // panel for the ToolFallback view above.
      } catch (error) {
        clearClarifyRequest(matchingRequest.requestId, matchingRequest.sessionId)
        const msg = error instanceof Error ? error.message : String(error)

        if (!msg.toLowerCase().includes('no pending')) {
          notifyError(error, copy.sendFailed)
        }

        setSubmitting(false)
        isSubmittingRef.current = false
      }
    },
    [copy.gatewayDisconnected, copy.notReady, copy.sendFailed, gateway, matchingRequest, ready, submitting]
  )

  const handleTextareaKey = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        const trimmed = draft.trim()

        if (trimmed) {
          void respond(trimmed)
        }
      }
    },
    [draft, respond]
  )

  const handleSubmitFreeform = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const trimmed = draft.trim()

      if (trimmed) {
        void respond(trimmed)
      }
    },
    [draft, respond]
  )

  return (
    <div
      className="relative mb-3 mt-2 grid gap-6 rounded-[0.5rem] border border-border/70 bg-card/40 px-3 py-2.5 text-sm shadow-[inset_0_1px_0_color-mix(in_srgb,var(--foreground)_3%,transparent)]"
      data-slot="clarify-inline"
    >
      <span aria-hidden className="arc-border" />
      <div className="flex items-start gap-2.5">
        <span
          aria-hidden
          className="mt-px grid size-6 shrink-0 place-items-center rounded-md bg-[color-mix(in_srgb,var(--dt-primary)_14%,transparent)] text-primary ring-1 ring-inset ring-primary/15"
        >
          <HelpCircle className="size-3.5" />
        </span>
        <span className="flex-1 whitespace-pre-wrap font-medium leading-snug text-foreground">
          {question || <em className="font-normal text-muted-foreground/70">{copy.loadingQuestion}</em>}
        </span>
      </div>

      {remainingMs !== null && (
        <div
          className={cn(
            'flex items-center gap-1.5 text-[0.6875rem] tabular-nums',
            remainingMs <= COUNTDOWN_URGENT_MS ? 'font-medium text-destructive' : 'text-muted-foreground/85'
          )}
          data-slot="clarify-countdown"
          data-urgent={remainingMs <= COUNTDOWN_URGENT_MS ? 'true' : undefined}
        >
          <Clock aria-hidden className="size-3" />
          <span>
            {remainingMs <= COUNTDOWN_URGENT_MS
              ? copy.timeRemainingSoon(formatCountdown(remainingMs))
              : copy.timeRemaining(formatCountdown(remainingMs))}
          </span>
        </div>
      )}

      {!typing && hasChoices && (
        <div className="grid gap-0.5" role="group">
          {choices.map((choice, index) => (
            <button
              className={cn(
                OPTION_ROW_CLASS,
                'text-foreground/95 hover:bg-accent/60 disabled:cursor-not-allowed disabled:opacity-55',
                selectedChoice === choice && 'bg-accent/60'
              )}
              data-choice
              disabled={!ready || submitting}
              key={`${index}-${choice}`}
              onClick={() => {
                setSelectedChoice(choice)
                void respond(choice)
              }}
              type="button"
            >
              <RadioDot selected={selectedChoice === choice} />
              <span className="flex-1 wrap-anywhere">{choice}</span>
              {selectedChoice === choice && <Check aria-hidden className="size-4 shrink-0 text-primary" />}
            </button>
          ))}
          <button
            className={cn(OPTION_ROW_CLASS, 'text-muted-foreground hover:bg-accent/40 hover:text-foreground')}
            disabled={submitting}
            onClick={() => {
              setTyping(true)
              window.setTimeout(() => textareaRef.current?.focus({ preventScroll: true }), 0)
            }}
            type="button"
          >
            <RadioDot selected={false} />
            <span className="flex-1">{copy.other}</span>
          </button>
        </div>
      )}

      {(typing || !hasChoices) && (
        <form className="grid gap-2" onSubmit={handleSubmitFreeform}>
          <Textarea
            className="min-h-20 resize-y rounded-lg border-transparent bg-accent/40 text-sm focus-visible:bg-background/60"
            disabled={submitting}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={handleTextareaKey}
            placeholder={copy.placeholder}
            ref={textareaRef}
            value={draft}
          />
          <div className="flex items-center justify-between gap-2">
            <span className="text-[0.6875rem] text-muted-foreground/85">{copy.shortcut}</span>
            <div className="flex items-center gap-1.5">
              {hasChoices && (
                <Button
                  disabled={submitting}
                  onClick={() => {
                    setTyping(false)
                    setDraft('')
                  }}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  {copy.back}
                </Button>
              )}
              <Button
                disabled={!ready || submitting}
                onClick={() => void respond('')}
                size="sm"
                type="button"
                variant="ghost"
              >
                {copy.skip}
              </Button>
              <Button disabled={!ready || submitting || !draft.trim()} size="sm" type="submit">
                {submitting ? <Loader2 className="size-3.5 animate-spin" /> : copy.send}
              </Button>
            </div>
          </div>
        </form>
      )}

      {!typing && hasChoices && (
        <div className="flex justify-end">
          <Button
            className="-mr-2"
            disabled={!ready || submitting}
            onClick={() => void respond('')}
            size="xs"
            type="button"
            variant="text"
          >
            {copy.skip}
          </Button>
        </div>
      )}
    </div>
  )
}
