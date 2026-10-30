'use client'

import { type ToolCallMessagePartProps } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { TopicLabel } from '@/components/assistant-ui/question-card'
import { ToolFallback } from '@/components/assistant-ui/tool-fallback'
import { Button } from '@/components/ui/button'
import { type Translations, useI18n } from '@/i18n'
import { clarifyArgsQuestions, type ClarifyOutcomeRow, readClarifyOutcome } from '@/lib/clarify'
import { Clock, HelpCircle } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $clarifyRequest, requestQuestions, revealQuestionCard } from '@/store/clarify'

export { formatCountdown } from '@/components/assistant-ui/question-card'

type ClarifyCopy = Translations['assistant']['clarify']

// AIS-333: the clarify tool result once the gateway stopped waiting. Any other
// settled result renders as the answered row (AIS-793) or, for an error,
// through ToolFallback.
interface ClarifyTimeoutResult {
  timeoutSeconds: number | null
  question: string
}

function resultRow(result: unknown): Record<string, unknown> | null {
  if (result && typeof result === 'object') {
    return result as Record<string, unknown>
  }

  if (typeof result === 'string' && result.trim().startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(result)

      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
    } catch {
      return null
    }
  }

  return null
}

export function readClarifyTimeout(result: unknown): ClarifyTimeoutResult | null {
  const row = resultRow(result)

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

export const ClarifyTool = (props: ToolCallMessagePartProps) => {
  if (props.result === undefined) {
    // The question waits in the card above the composer (AIS-793).
    return <ClarifyAnchor args={props.args} />
  }

  const timedOut = readClarifyTimeout(props.result)

  // AIS-333: an expired question used to collapse into the generic tool
  // block — the user saw it "disappear" (SUP-20260914-092956). Keep it as
  // a visible card that says what happened.
  if (timedOut) {
    return (
      <ClarifyToolTimedOut
        question={timedOut.question || clarifyArgsQuestions(props.args)[0]?.question || ''}
        timeoutSeconds={timedOut.timeoutSeconds}
      />
    )
  }

  const rows = readClarifyOutcome(props.args, props.result)

  return rows?.length ? <ClarifyAnswered rows={rows} /> : <ToolFallback {...props} />
}

function ClarifyAnchor({ args }: { args: unknown }) {
  const { t } = useI18n()
  const copy = t.assistant.clarify
  const request = useStore($clarifyRequest)
  const asked = useMemo(() => clarifyArgsQuestions(args), [args])
  const questions = request ? requestQuestions(request) : asked
  const count = Math.max(questions.length, 1)
  const topics = questions.map(item => item.topic).filter(Boolean)
  const preview = topics.length ? topics.join(' · ') : questions[0]?.question || ''

  return (
    <div className="my-1.5 flex min-w-0 items-center gap-2 text-sm" data-slot="clarify-anchor">
      <span
        aria-hidden
        className="grid size-5 shrink-0 place-items-center rounded-md bg-[color-mix(in_srgb,var(--dt-primary)_14%,transparent)] text-primary ring-1 ring-inset ring-primary/15"
      >
        <HelpCircle className="size-3" />
      </span>
      <span className="shrink-0 font-medium text-foreground">{copy.asked(count)}</span>
      {preview ? (
        <span className="min-w-0 truncate text-muted-foreground">{preview}</span>
      ) : (
        <em className="text-muted-foreground/70">{copy.loadingQuestion}</em>
      )}
      {request && (
        <Button
          className="shrink-0"
          onClick={() => revealQuestionCard(request.requestId)}
          size="xs"
          type="button"
          variant="textStrong"
        >
          {copy.showQuestions(count)}
        </Button>
      )}
    </div>
  )
}

function answerText(row: ClarifyOutcomeRow, copy: ClarifyCopy): string {
  switch (row.state) {
    case 'answered':
      return row.response || copy.answerEmpty

    case 'skipped':
      return copy.answerLater

    case 'timeout':
      return copy.answerTimeout

    case 'delivery_failed':

    case 'unavailable':
      return copy.answerUndelivered

    default:
      return copy.answerEmpty
  }
}

/** AIS-793: a finished question stays readable — question → answer, at full strength. */
function ClarifyAnswered({ rows }: { rows: ClarifyOutcomeRow[] }) {
  const { t } = useI18n()
  const copy = t.assistant.clarify

  return (
    <div className="min-w-0 max-w-full" data-slot="tool-block" data-tool="clarify">
      <div className="grid gap-1.5 text-[length:var(--conversation-tool-font-size)]" data-slot="clarify-answered">
        <div className="flex items-center gap-1.5 text-(--ui-text-tertiary)">
          <HelpCircle aria-hidden className="size-3.5 shrink-0" />
          <span>{copy.title(rows.length)}</span>
        </div>
        <dl className="grid gap-1.5 border-l border-(--ui-stroke-tertiary) pl-3">
          {rows.map((row, index) => (
            <div className="grid min-w-0 gap-0.5" key={`${index}-${row.question}`}>
              <dt className="flex min-w-0 items-start gap-1.5 text-muted-foreground">
                <TopicLabel topic={row.topic} />
                <span className="min-w-0 whitespace-pre-wrap">{row.question}</span>
              </dt>
              <dd
                className={cn(
                  'whitespace-pre-wrap wrap-anywhere',
                  row.state === 'answered' && row.response ? 'text-foreground' : 'italic text-muted-foreground'
                )}
                data-state={row.state}
              >
                {answerText(row, copy)}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
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
          <p className="text-[0.8125rem] leading-snug text-muted-foreground">
            {copy.timedOutBody(timeoutSeconds ?? 0)}
          </p>
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
