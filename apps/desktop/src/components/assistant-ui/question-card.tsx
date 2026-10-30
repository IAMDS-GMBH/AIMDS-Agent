'use client'

import { useStore } from '@nanostores/react'
import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { type Translations, useI18n } from '@/i18n'
import type { ClarifyAnswer, ClarifyQuestion } from '@/lib/clarify'
import { triggerHaptic } from '@/lib/haptics'
import { Check, ChevronDown, Clock, HelpCircle, Loader2, Pencil } from '@/lib/icons'
import { formatCombo, isModKey } from '@/lib/keybinds/combo'
import { cn } from '@/lib/utils'
import {
  $clarifyDrafts,
  $clarifyRequest,
  $questionCardFocus,
  $questionCardMinimized,
  type ClarifyRequest,
  clearClarifyRequest,
  type QuestionDraft,
  requestQuestions,
  revealQuestionCard,
  setQuestionCardMinimized,
  setQuestionDraft
} from '@/store/clarify'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'

// AIS-793: the question card docks directly above the composer, so a question
// no longer waits far up the thread. It shows every question of the ask (topic,
// choices, own answer, "later"); the thread keeps a small anchor that brings
// it back, and a minimised card leaves a chip with the open count.

type ClarifyCopy = Translations['assistant']['clarify']

const COUNTDOWN_URGENT_MS = 30_000

// Choice and "Other" buttons share a layout; only border/color differs.
const CHOICE_CLASS =
  'inline-flex max-w-full items-center gap-1.5 rounded-md border px-2.5 py-1 text-left text-[0.8125rem] leading-snug transition-colors disabled:cursor-not-allowed disabled:opacity-55'

export function formatCountdown(remainingMs: number): string {
  const total = Math.max(0, Math.ceil(remainingMs / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60

  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
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

export function TopicLabel({ topic }: { topic?: string }) {
  if (!topic) {
    return null
  }

  return (
    <span
      className="inline-flex max-w-40 shrink-0 truncate rounded-[0.3125rem] bg-accent/70 px-1.5 py-px text-[0.6875rem] font-medium leading-4 text-muted-foreground ring-1 ring-inset ring-border/60"
      data-slot="clarify-topic"
    >
      {topic}
    </span>
  )
}

function answerLabel(answer: ClarifyAnswer, copy: ClarifyCopy): string {
  return answer.state === 'skipped' ? copy.later : answer.response
}

/** Docked above the composer: the active session's open questions. */
export function QuestionCard() {
  const request = useStore($clarifyRequest)

  if (!request) {
    return null
  }

  return <QuestionCardFor key={request.requestId} request={request} />
}

function QuestionCardFor({ request }: { request: ClarifyRequest }) {
  const { t } = useI18n()
  const copy = t.assistant.clarify
  const gateway = useStore($gateway)
  const drafts = useStore($clarifyDrafts)[request.requestId]
  const minimized = useStore($questionCardMinimized)[request.requestId] ?? false
  const focus = useStore($questionCardFocus)
  const questions = useMemo(() => requestQuestions(request), [request])
  const remainingMs = useCountdown(request.deadlineAt)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const [editing, setEditing] = useState<number | null>(null)
  const cardRef = useRef<HTMLElement | null>(null)
  const single = questions.length === 1
  const draftFor = (index: number): QuestionDraft => drafts?.[index] ?? { answer: null, text: '' }
  const answers = questions.map((_, index) => drafts?.[index]?.answer ?? null)
  const done = answers.filter(Boolean).length
  const open = questions.length - done

  const focusQuestion = useCallback((index: number | null) => {
    const root = cardRef.current

    if (!root) {
      return
    }

    const block =
      index === null
        ? root.querySelector<HTMLElement>('[data-slot="clarify-question"][data-open]')
        : root.querySelector<HTMLElement>(`[data-slot="clarify-question"][data-index="${index}"]`)

    block?.querySelector<HTMLElement>('button, textarea')?.focus({ preventScroll: true })
  }, [])

  // The anchor or the chip asked for the card: show it and focus the first open question.
  useEffect(() => {
    if (!focus || focus.requestId !== request.requestId || minimized) {
      return
    }

    cardRef.current?.scrollIntoView?.({ block: 'nearest' })
    window.setTimeout(() => focusQuestion(null), 0)
  }, [focus, focusQuestion, minimized, request.requestId])

  const send = useCallback(
    async (final: ClarifyAnswer[]) => {
      if (submittingRef.current) {
        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed)

        return
      }

      submittingRef.current = true
      setSubmitting(true)

      try {
        // `answers` is the clarify_questions contract; `answer` keeps the
        // single form readable for an older backend.
        await gateway.request<{ ok?: boolean }>('clarify.respond', {
          request_id: request.requestId,
          answer: final.length === 1 && final[0]!.state === 'answered' ? final[0]!.response : '',
          answers: final
        })
        triggerHaptic('submit')
        clearClarifyRequest(request.requestId, request.sessionId)
        // The matching tool.complete swaps the thread's anchor for the answered row.
      } catch (error) {
        clearClarifyRequest(request.requestId, request.sessionId)
        const msg = error instanceof Error ? error.message : String(error)

        if (!msg.toLowerCase().includes('no pending')) {
          notifyError(error, copy.sendFailed)
        }

        submittingRef.current = false
        setSubmitting(false)
      }
    },
    [copy.gatewayDisconnected, copy.sendFailed, gateway, request.requestId, request.sessionId]
  )

  const answer = (index: number, value: ClarifyAnswer) => {
    if (single) {
      void send([value])

      return
    }

    setQuestionDraft(request.requestId, index, { answer: value })
    setEditing(null)
    // Move on to the next open question.
    const next = questions.findIndex((_, i) => i !== index && !answers[i])

    if (next >= 0) {
      window.setTimeout(() => focusQuestion(next), 0)
    }
  }

  const sendAll = () => {
    void send(answers.map(given => given ?? { response: '', state: 'skipped' }))
  }

  if (minimized) {
    return (
      <div className="relative z-5 mx-1 mb-1.5 flex">
        <button
          className="inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-[color-mix(in_srgb,var(--dt-card)_92%,transparent)] px-2.5 py-1 text-xs font-medium text-foreground shadow-xs transition-colors hover:bg-accent/60"
          data-slot="question-chip"
          onClick={() => revealQuestionCard(request.requestId)}
          type="button"
        >
          <HelpCircle aria-hidden className="size-3.5 text-primary" />
          {copy.openQuestions(Math.max(open, 1))}
        </button>
      </div>
    )
  }

  return (
    <section
      aria-label={copy.title(questions.length)}
      className="relative z-5 mx-1 mb-2 grid max-h-[min(60vh,36rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-3 rounded-xl border border-border/70 bg-[color-mix(in_srgb,var(--dt-card)_94%,transparent)] px-3 py-2.5 text-sm shadow-[0_0.25rem_1rem_color-mix(in_srgb,var(--foreground)_6%,transparent)] backdrop-blur-[0.75rem]"
      data-slot="question-card"
      ref={cardRef}
    >
      <span aria-hidden className="arc-border" />
      <header className="flex items-center gap-2">
        <span
          aria-hidden
          className="grid size-6 shrink-0 place-items-center rounded-md bg-[color-mix(in_srgb,var(--dt-primary)_14%,transparent)] text-primary ring-1 ring-inset ring-primary/15"
        >
          <HelpCircle className="size-3.5" />
        </span>
        <span className="font-medium text-foreground">{copy.title(questions.length)}</span>
        {!single && (
          <span className="text-[0.6875rem] tabular-nums text-muted-foreground/85">
            {copy.progress(done, questions.length)}
          </span>
        )}
        <span className="flex-1" />
        {remainingMs !== null && (
          <span
            className={cn(
              'flex items-center gap-1.5 text-[0.6875rem] tabular-nums',
              remainingMs <= COUNTDOWN_URGENT_MS ? 'font-medium text-destructive' : 'text-muted-foreground/85'
            )}
            data-slot="clarify-countdown"
            data-urgent={remainingMs <= COUNTDOWN_URGENT_MS ? 'true' : undefined}
          >
            <Clock aria-hidden className="size-3" />
            {remainingMs <= COUNTDOWN_URGENT_MS
              ? copy.timeRemainingSoon(formatCountdown(remainingMs))
              : copy.timeRemaining(formatCountdown(remainingMs))}
          </span>
        )}
        <Button
          aria-label={copy.minimize}
          className="-mr-1"
          onClick={() => setQuestionCardMinimized(request.requestId, true)}
          size="icon-xs"
          title={copy.minimize}
          type="button"
          variant="ghost"
        >
          <ChevronDown />
        </Button>
      </header>

      {/* Header and send button stay put; only the questions scroll. */}
      <div className="-mx-3 grid min-h-0 gap-3 overflow-y-auto px-3">
        {questions.map((item, index) => (
          <QuestionBlock
            copy={copy}
            draft={draftFor(index)}
            editing={editing === index}
            index={index}
            item={item}
            key={`${index}-${item.question}`}
            onAnswer={value => answer(index, value)}
            onEdit={() => {
              setEditing(index)
              window.setTimeout(() => focusQuestion(index), 0)
            }}
            onText={text => setQuestionDraft(request.requestId, index, { text })}
            single={single}
            submitting={submitting}
          />
        ))}
      </div>

      {!single && (
        <footer className="flex items-center justify-end gap-2">
          {submitting && <Loader2 aria-hidden className="size-3.5 animate-spin text-muted-foreground" />}
          <Button disabled={submitting || done === 0} onClick={sendAll} size="sm" type="button">
            {open > 0 && done > 0 ? copy.sendRestLater(open) : copy.sendAnswers}
          </Button>
        </footer>
      )}
    </section>
  )
}

interface QuestionBlockProps {
  copy: ClarifyCopy
  draft: QuestionDraft
  editing: boolean
  index: number
  item: ClarifyQuestion
  onAnswer: (value: ClarifyAnswer) => void
  onEdit: () => void
  onText: (text: string) => void
  single: boolean
  submitting: boolean
}

function QuestionBlock({
  copy,
  draft,
  editing,
  index,
  item,
  onAnswer,
  onEdit,
  onText,
  single,
  submitting
}: QuestionBlockProps) {
  const choices = item.choices ?? []
  const hasChoices = choices.length > 0
  // A half-typed own answer survives minimising and switching chats.
  const [typing, setTyping] = useState(() => Boolean(draft.text))
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const given = draft.answer

  if (given && !editing) {
    // Answered (or "later") on this card: one readable line, still changeable.
    return (
      <div
        className="flex min-w-0 items-center gap-2 rounded-md bg-accent/30 px-2.5 py-1.5"
        data-index={index}
        data-slot="clarify-question"
      >
        <Check aria-hidden className="size-3.5 shrink-0 text-primary" />
        <TopicLabel topic={item.topic} />
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{item.question}</span>
        <span
          className={cn(
            'max-w-[45%] shrink-0 truncate',
            given.state === 'skipped' ? 'italic text-muted-foreground' : 'text-foreground'
          )}
        >
          {answerLabel(given, copy)}
        </span>
        <Button className="-mr-1" disabled={submitting} onClick={onEdit} size="xs" type="button" variant="text">
          {copy.change}
        </Button>
      </div>
    )
  }

  const submitText = () => {
    const text = draft.text.trim()

    if (text) {
      onAnswer({ response: text, state: 'answered' })
      setTyping(false)
    }
  }

  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && isModKey(event)) {
      event.preventDefault()
      submitText()
    }
  }

  const showText = typing || !hasChoices

  const later = (
    <Button
      className="-mr-1.5 ml-auto"
      disabled={submitting}
      onClick={() => onAnswer({ response: '', state: 'skipped' })}
      size="xs"
      type="button"
      variant="text"
    >
      {copy.later}
    </Button>
  )

  return (
    <div className="grid gap-1.5" data-index={index} data-open="" data-slot="clarify-question">
      <div className="flex items-start gap-2">
        <TopicLabel topic={item.topic} />
        <span className="flex-1 whitespace-pre-wrap font-medium leading-snug text-foreground">{item.question}</span>
      </div>

      {!showText && (
        // Choices as one wrapping row, so several questions fit the card.
        <div className="flex flex-wrap items-center gap-1.5" role="group">
          {choices.map((choice, choiceIndex) => {
            const selected = given?.state === 'answered' && given.response === choice

            return (
              <button
                aria-pressed={selected}
                className={cn(
                  CHOICE_CLASS,
                  'border-border/80 text-foreground/95 hover:border-primary/40 hover:bg-accent/60',
                  selected && 'border-primary/50 bg-accent/60'
                )}
                data-choice
                disabled={submitting}
                key={`${choiceIndex}-${choice}`}
                onClick={() => onAnswer({ response: choice, state: 'answered' })}
                type="button"
              >
                {selected && <Check aria-hidden className="size-3.5 shrink-0 text-primary" />}
                <span className="wrap-anywhere">{choice}</span>
              </button>
            )
          })}
          <button
            className={cn(
              CHOICE_CLASS,
              'border-dashed border-border/80 text-muted-foreground hover:bg-accent/40 hover:text-foreground'
            )}
            disabled={submitting}
            onClick={() => {
              setTyping(true)
              window.setTimeout(() => textareaRef.current?.focus({ preventScroll: true }), 0)
            }}
            type="button"
          >
            <Pencil aria-hidden className="size-3.5 shrink-0" />
            <span>{copy.other}</span>
          </button>
          {later}
        </div>
      )}

      {showText && (
        <>
          <Textarea
            className="min-h-14 resize-y rounded-lg border-transparent bg-accent/40 text-sm focus-visible:bg-background/60"
            disabled={submitting}
            onChange={event => onText(event.target.value)}
            onKeyDown={handleKey}
            placeholder={copy.placeholder}
            ref={textareaRef}
            rows={2}
            value={draft.text}
          />
          <div className="flex items-center gap-1.5">
            {single && (
              <span className="text-[0.6875rem] text-muted-foreground/85">
                {copy.shortcut(formatCombo('mod+enter'))}
              </span>
            )}
            {later}
            {hasChoices && (
              <Button disabled={submitting} onClick={() => setTyping(false)} size="xs" type="button" variant="ghost">
                {copy.back}
              </Button>
            )}
            <Button disabled={submitting || !draft.text.trim()} onClick={submitText} size="xs" type="button">
              {submitting && single ? <Loader2 className="size-3.5 animate-spin" /> : single ? copy.send : copy.apply}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
