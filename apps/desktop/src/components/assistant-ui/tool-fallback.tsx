'use client'

import { type ToolCallMessagePartProps, useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { createContext, type FC, type PropsWithChildren, type ReactNode, useContext, useMemo } from 'react'

import { AnsiText } from '@/components/assistant-ui/ansi-text'
import { useElapsedSeconds } from '@/components/chat/activity-timer'
import { ActivityTimerText } from '@/components/chat/activity-timer-text'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { DiffLines } from '@/components/chat/diff-lines'
import { DisclosureRow } from '@/components/chat/disclosure-row'
import { PreviewAttachment } from '@/components/chat/preview-attachment'
import { ZoomableImage } from '@/components/chat/zoomable-image'
import { BrailleSpinner } from '@/components/ui/braille-spinner'
import { CopyButton } from '@/components/ui/copy-button'
import { FadeText } from '@/components/ui/fade-text'
import { ToolIcon } from '@/components/ui/tool-icon'
import { useI18n } from '@/i18n'
import { PrettyLink, LinkifiedText as SharedLinkifiedText, urlSlugTitleLabel } from '@/lib/external-link'
import { AlertCircle, CheckCircle2 } from '@/lib/icons'
import { serviceDisplayName, toolLabel } from '@/lib/tool-labels'
import { midTurnPhaseLine } from '@/lib/turn-phase-copy'
import { useEnterAnimation } from '@/lib/use-enter-animation'
import { cn } from '@/lib/utils'
import { $activeSessionId } from '@/store/session'
import { $subagentsBySession, type SubagentProgress } from '@/store/subagents'
import { $toolInlineDiffs } from '@/store/tool-diffs'
import { $toolDisclosureOpen, $toolViewMode, setToolDisclosureOpen } from '@/store/tool-view'
import { $turnPhaseBySession } from '@/store/turn-phase'

import { subagentRuns, type SubagentRunView, summaryLine } from './subagent-runs'
import { useAwaitingApproval } from './tool-approval'
import {
  activitySummary,
  buildToolView,
  cleanVisibleText,
  formatActivityDuration,
  inlineDiffFromResult,
  isPreviewableTarget,
  looksRedundant,
  type SearchResultRow,
  selectMessageRunning,
  stripInlineDiffChrome,
  toolCopyPayload,
  type ToolPart,
  toolPartDisclosureId,
  type ToolStatus,
  toolStepRuns
} from './tool-fallback-model'

// `true` when a ToolEntry is rendered inside an embedding wrapper that owns
// the per-row chrome (timer / preview). The flat ToolGroupSlot sets this
// false, so every row currently owns its own chrome; kept as a seam for any
// future embedding surface.
const ToolEmbedContext = createContext(false)

// Shared header chrome for tool rows. Both the single-tool DisclosureRow
// and the multi-tool group header pass through these constants so a
// "Patch" row and a "Tool actions · 2 steps" row are visually identical.
// The thread's wait and stall lines use them too (AIS-787).
export const TOOL_HEADER_TITLE_CLASS =
  'text-[length:var(--conversation-tool-font-size)] font-medium leading-(--conversation-line-height) text-(--ui-text-secondary)'

export const TOOL_HEADER_DURATION_CLASS = 'shrink-0 text-[0.625rem] tabular-nums text-(--ui-text-tertiary)'

export const TOOL_HEADER_SUBTITLE_CLASS =
  'text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)'

const TOOL_HEADER_GLYPH_WRAP_CLASS = 'grid size-3.5 shrink-0 place-items-center self-center'

// Glass-style section label that sits above any pre/JSON/output block.
// Lowercase tracking + tiny size so it reads as a quiet field label rather
// than a chrome heading. Used for "COMMAND OUTPUT", "INPUT", "OUTPUT", etc.
const TOOL_SECTION_LABEL_CLASS = 'mb-1 text-[0.65rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)'

// Inset scroll surface for any detail body. The expanded tool row owns the
// border; the payload itself is just clipped raw text.
const TOOL_SECTION_SURFACE_CLASS =
  'max-h-20 max-w-full overflow-auto bg-transparent px-2 py-1.5 text-(--ui-text-secondary)'

const TOOL_SECTION_PRE_CLASS = cn(TOOL_SECTION_SURFACE_CLASS, 'font-mono text-[0.7rem] leading-relaxed')

interface ToolStatusCopy {
  statusDone: string
  statusError: string
  statusRecovered: string
  statusRunning: string
}

function rawTechnicalTrace(args: unknown, result: unknown): string {
  const parts = [args, result]
    .filter(value => value !== undefined && value !== null)
    .map(value => {
      if (typeof value === 'string') {
        return value
      }

      try {
        return JSON.stringify(value)
      } catch {
        return String(value)
      }
    })
    .filter(Boolean)

  return parts.join('\n')
}

function statusGlyph(status: ToolStatus, copy: ToolStatusCopy): ReactNode {
  if (status === 'running') {
    return (
      <BrailleSpinner
        ariaLabel={copy.statusRunning}
        className="size-3.5 shrink-0 text-[0.95rem] text-(--ui-text-tertiary)"
        spinner="breathe"
      />
    )
  }

  if (status === 'error') {
    return <AlertCircle aria-label={copy.statusError} className="size-3.5 shrink-0 text-destructive" />
  }

  if (status === 'warning') {
    return (
      <AlertCircle
        aria-label={copy.statusRecovered}
        className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400"
      />
    )
  }

  return (
    <CheckCircle2
      aria-label={copy.statusDone}
      className="size-3.5 shrink-0 text-emerald-600/85 dark:text-emerald-400/85"
    />
  )
}

// Leading glyph for any tool-row header. Status (running/error/warning)
// takes precedence; otherwise falls back to the tool's codicon. Returns
// null when neither applies so callers can render unconditionally.
function ToolGlyph({ copy, icon, status }: { copy: ToolStatusCopy; icon?: string; status?: ToolStatus }) {
  const node = status ? (
    statusGlyph(status, copy)
  ) : icon ? (
    <ToolIcon className="text-(--ui-text-tertiary)" name={icon} size="0.875rem" />
  ) : null

  return node ? <span className={TOOL_HEADER_GLYPH_WRAP_CLASS}>{node}</span> : null
}

// Which status (if any) should pre-empt the tool's icon in the leading
// slot. Success is silent — the row reads as "done" without a checkmark.
function leadingStatus(isPending: boolean, status: ToolStatus): ToolStatus | undefined {
  if (isPending) {
    return 'running'
  }

  return status === 'success' ? undefined : status
}

function SearchResultsList({ hits }: { hits: SearchResultRow[] }) {
  return (
    <ol className="m-0 grid list-none gap-2.5 p-0">
      {hits.map((hit, index) => {
        const key = `${hit.url || hit.title}-${index}`
        const trimmedTitle = hit.title.trim()

        return (
          <li className="grid min-w-0 gap-0.5" key={key}>
            {hit.url ? (
              <PrettyLink
                className={cn(TOOL_HEADER_TITLE_CLASS, 'block max-w-full')}
                fallbackLabel={trimmedTitle || urlSlugTitleLabel(hit.url)}
                href={hit.url}
                label={trimmedTitle || undefined}
              />
            ) : (
              <span className={TOOL_HEADER_TITLE_CLASS}>{trimmedTitle}</span>
            )}
            {hit.snippet && <p className={cn(TOOL_HEADER_SUBTITLE_CLASS, 'm-0 line-clamp-3')}>{hit.snippet}</p>}
          </li>
        )
      })}
    </ol>
  )
}

function LinkifiedText({ className, text }: { className?: string; text: string }) {
  return <SharedLinkifiedText className={className} pretty text={cleanVisibleText(text)} />
}

interface ToolEntryProps {
  part: ToolPart
}

function useDisclosureOpen(disclosureId: string, fallbackOpen = false): boolean {
  const persistedOpen = useStore($toolDisclosureOpen(disclosureId))

  return persistedOpen ?? fallbackOpen
}

function ToolEntry({ part }: ToolEntryProps) {
  const { t } = useI18n()
  const copy = t.assistant.tool
  const messageId = useAuiState(s => s.message.id)
  const messageRunning = useAuiState(selectMessageRunning)
  const embedded = useContext(ToolEmbedContext)
  const toolViewMode = useStore($toolViewMode)
  const disclosureId = `tool-entry:${messageId}:${toolPartDisclosureId(part)}`
  const open = useDisclosureOpen(disclosureId)
  const isPending = messageRunning && part.result === undefined
  // Only animate entries that mount while their message is actively
  // streaming — historical sessions mount with `messageRunning === false`,
  // so they paint statically without a settle cascade. The wrapping group
  // handles its own enter animation, so embedded children skip it.
  const enterRef = useEnterAnimation(messageRunning && !embedded, `tool-entry:${disclosureId}`)
  const elapsed = useElapsedSeconds(isPending, `tool:${disclosureId}`)
  const liveDiffs = useStore($toolInlineDiffs)
  const sideDiff = part.toolCallId ? liveDiffs[part.toolCallId] || '' : ''
  const inlineDiff = stripInlineDiffChrome(sideDiff) || inlineDiffFromResult(part.result)

  // Stale parts (no result, but message stopped running) get a synthetic
  // empty result so buildToolView treats them as completed-no-output.
  const view = useMemo(() => {
    const p = !isPending && part.result === undefined ? { ...part, result: {} } : part

    return buildToolView(p, inlineDiff)
  }, [inlineDiff, isPending, part])

  const detailSections = useMemo(() => {
    if (!view.detail) {
      return { body: '', summary: '' }
    }

    if (view.status !== 'error') {
      return { body: view.detail, summary: '' }
    }

    const chunks = view.detail
      .split(/\n\s*\n+/)
      .map(chunk => chunk.trim())
      .filter(Boolean)

    const [summary = '', ...rest] = chunks
    const subtitleNorm = view.subtitle.trim().toLowerCase()
    const summaryDuplicatesSubtitle = summary && summary.toLowerCase() === subtitleNorm

    if (summaryDuplicatesSubtitle) {
      return { body: rest.join('\n\n').trim(), summary: '' }
    }

    return { body: rest.join('\n\n').trim(), summary }
  }, [view.detail, view.status, view.subtitle])

  const detailMatchesSubtitle = looksRedundant(view.subtitle, view.detail)

  const showDetail =
    (view.status === 'error' && Boolean(detailSections.summary || detailSections.body)) ||
    (view.status !== 'error' &&
      Boolean(view.detail) &&
      !looksRedundant(view.title, view.detail) &&
      !detailMatchesSubtitle)

  const renderDetailAsCode =
    view.status !== 'error' &&
    (part.toolName === 'terminal' || part.toolName === 'execute_code' || part.toolName === 'read_file')

  const hasSearchHits = Boolean(view.searchHits?.length)
  const searchResultsLabel = part.toolName === 'web_search' ? 'Search results' : view.detailLabel

  const showRawSearchDrilldown =
    part.toolName === 'web_search' &&
    part.result !== undefined &&
    toolViewMode !== 'technical' &&
    Boolean(view.rawResult.trim())

  const hasExpandableContent = Boolean(
    (view.previewTarget && isPreviewableTarget(view.previewTarget)) ||
    view.imageUrl ||
    view.inlineDiff ||
    showDetail ||
    hasSearchHits ||
    toolViewMode === 'technical'
  )

  const copyAction = useMemo(() => toolCopyPayload(part, view), [part, view])

  const trailing =
    isPending && !embedded ? (
      <ActivityTimerText className={TOOL_HEADER_DURATION_CLASS} seconds={elapsed} />
    ) : !isPending && copyAction.text ? (
      <CopyButton appearance="tool-row" label={copyAction.label} stopPropagation text={copyAction.text} />
    ) : undefined

  return (
    <div
      className={cn(
        'min-w-0 max-w-full overflow-hidden text-[length:var(--conversation-tool-font-size)] text-(--ui-text-tertiary)',
        open && 'rounded-[0.625rem] border border-(--ui-stroke-tertiary)'
      )}
      data-slot="tool-block"
      ref={enterRef}
    >
      <div className={cn(open && 'border-b border-(--ui-stroke-tertiary) px-2 py-1.5')}>
        <DisclosureRow
          onToggle={hasExpandableContent ? () => setToolDisclosureOpen(disclosureId, !open) : undefined}
          open={open}
          trailing={trailing}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <ToolGlyph copy={copy} icon={view.icon} status={leadingStatus(isPending, view.status)} />
            <FadeText
              className={cn(
                TOOL_HEADER_TITLE_CLASS,
                isPending && 'shimmer text-(--ui-text-tertiary)',
                view.status === 'error' && 'text-destructive',
                view.status === 'warning' && 'text-amber-700 dark:text-amber-300'
              )}
            >
              {view.title}
            </FadeText>
            {!isPending && view.countLabel && <span className={TOOL_HEADER_DURATION_CLASS}>{view.countLabel}</span>}
            {!isPending && view.durationLabel && (
              <span className={TOOL_HEADER_DURATION_CLASS}>{view.durationLabel}</span>
            )}
          </span>
        </DisclosureRow>
      </div>
      {open && (
        <div className="grid w-full min-w-0 max-w-full gap-1.5 overflow-hidden p-1.5">
          {!embedded && view.previewTarget && isPreviewableTarget(view.previewTarget) && (
            <PreviewAttachment source="tool-result" target={view.previewTarget} />
          )}
          {view.imageUrl && (
            <div className="max-w-72 overflow-hidden rounded-[0.25rem] border border-(--ui-stroke-tertiary)">
              <ZoomableImage alt={copy.outputAlt} className="h-auto w-full object-cover" src={view.imageUrl} />
            </div>
          )}
          {hasSearchHits && view.searchHits && (
            <div className="max-w-full text-xs leading-relaxed text-(--ui-text-secondary)">
              {searchResultsLabel && <p className={TOOL_SECTION_LABEL_CLASS}>{searchResultsLabel}</p>}
              <SearchResultsList hits={view.searchHits} />
            </div>
          )}
          {showDetail &&
            toolViewMode !== 'technical' &&
            (view.status === 'error' ? (
              detailSections.summary || detailSections.body ? (
                <div className="max-w-full text-xs leading-relaxed text-destructive">
                  {detailSections.summary && (
                    <LinkifiedText className="block font-medium" text={detailSections.summary} />
                  )}
                  {detailSections.body && (
                    <pre
                      className={cn(
                        'max-h-56 overflow-auto whitespace-pre-wrap wrap-anywhere font-mono text-[0.7rem] leading-[1.55] text-destructive/90',
                        detailSections.summary && 'mt-1.5'
                      )}
                    >
                      {detailSections.body}
                    </pre>
                  )}
                </div>
              ) : null
            ) : view.stdout || view.stderr ? (
              // Stdout + stderr split: render both as labeled blocks. stderr
              // is intentionally NOT painted destructive — many CLIs log
              // informational output there.
              <div className="max-w-full text-xs leading-relaxed text-(--ui-text-secondary)">
                {view.detailLabel && <p className={TOOL_SECTION_LABEL_CLASS}>{view.detailLabel}</p>}
                {view.stdout && (
                  <div className="space-y-0.5">
                    {view.stderr && <p className={TOOL_SECTION_LABEL_CLASS}>stdout</p>}
                    <pre className={cn(TOOL_SECTION_PRE_CLASS, 'whitespace-pre-wrap wrap-anywhere')}>
                      {view.rendersAnsi ? <AnsiText text={view.stdout} /> : view.stdout}
                    </pre>
                  </div>
                )}
                {view.stderr && (
                  <div className={cn('space-y-0.5', view.stdout && 'mt-1.5')}>
                    <p className={TOOL_SECTION_LABEL_CLASS}>stderr</p>
                    <pre
                      className={cn(
                        TOOL_SECTION_PRE_CLASS,
                        'whitespace-pre-wrap wrap-anywhere text-(--ui-text-tertiary)'
                      )}
                    >
                      {view.rendersAnsi ? <AnsiText text={view.stderr} /> : view.stderr}
                    </pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="max-w-full text-xs leading-relaxed text-(--ui-text-secondary)">
                {view.detailLabel && <p className={TOOL_SECTION_LABEL_CLASS}>{view.detailLabel}</p>}
                {renderDetailAsCode ? (
                  <pre className={cn(TOOL_SECTION_PRE_CLASS, 'whitespace-pre-wrap wrap-anywhere')}>
                    {view.rendersAnsi ? <AnsiText text={view.detail} /> : view.detail}
                  </pre>
                ) : (
                  <CompactMarkdown className={cn(TOOL_SECTION_SURFACE_CLASS, 'wrap-anywhere')} text={view.detail} />
                )}
              </div>
            ))}
          {showRawSearchDrilldown && (
            <details className="max-w-full">
              <summary className={cn(TOOL_SECTION_LABEL_CLASS, 'mb-0')}>{copy.rawResponse}</summary>
              <pre className={cn(TOOL_SECTION_PRE_CLASS, 'mt-1 whitespace-pre-wrap wrap-anywhere')}>
                {view.rawResult}
              </pre>
            </details>
          )}
          {toolViewMode === 'technical' && (
            <pre className={cn(TOOL_SECTION_PRE_CLASS, 'whitespace-pre-wrap wrap-anywhere')}>
              {rawTechnicalTrace(part.args, part.result)}
            </pre>
          )}
        </div>
      )}
      {open && view.inlineDiff && <DiffLines text={view.inlineDiff} />}
    </div>
  )
}

/**
 * Flat, Cursor-style tool list. assistant-ui hands us a *range* of
 * consecutive tool-call parts, but how that range is sliced is unstable: a
 * live stream interleaves narration/reasoning between calls (many tiny
 * ranges), while the settled message reconstructs every tool_call back-to-back
 * (one big range). Rendering a "Tool actions · N steps" group off that range
 * therefore reshuffled the whole turn the instant it settled.
 *
 * So we never group: each tool is a standalone row, and the wrapper just lays
 * its children out on the tight `--tool-row-gap` rhythm. One range or ten,
 * fragmented or consecutive, the result is pixel-identical — a tight, stable
 * stack. The wrapper stays a single `<div>` of stable identity so children
 * never remount as the range grows mid-stream. `ToolEmbedContext` is false so
 * every row owns its own chrome (timer / preview / copy / inline approval).
 */
export const ToolGroupSlot: FC<PropsWithChildren<{ endIndex: number; startIndex: number }>> = ({
  children,
  startIndex
}) => {
  const messageId = useAuiState(s => s.message.id)
  const messageRunning = useAuiState(selectMessageRunning)
  const enterRef = useEnterAnimation(messageRunning, `tool-group:${messageId}:${startIndex}`)

  return (
    <ToolEmbedContext.Provider value={false}>
      <div
        className="grid min-w-0 max-w-full gap-(--tool-row-gap) overflow-hidden"
        data-slot="tool-block"
        ref={enterRef}
      >
        {children}
      </div>
    </ToolEmbedContext.Provider>
  )
}

// A running step this long without an answer gets a "still waiting" hint.
const ACTIVITY_SLOW_S = 15

function stringArg(args: unknown, key: string): string {
  const value = args && typeof args === 'object' ? (args as Record<string, unknown>)[key] : undefined

  return typeof value === 'string' ? value : ''
}

function numberArg(args: unknown, key: string): number {
  const value = args && typeof args === 'object' ? (args as Record<string, unknown>)[key] : undefined

  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** The sentence for a context summary, retry or model switch while the active
 *  chat's turn already shows output (AIS-786); empty otherwise. */
export function useMidTurnPhaseLine(): string {
  const { locale } = useI18n()
  const sessionId = useStore($activeSessionId)
  const phases = useStore($turnPhaseBySession)

  return midTurnPhaseLine(sessionId ? phases[sessionId] : undefined, locale)
}

/**
 * One block for a run of consecutive tool steps (AIS-744, per run since
 * AIS-786). While the turn works on this run — it is the message's tail, or
 * one of its steps has not answered yet — the block says in one line what is
 * happening right now ("Working on it · <step>"), with a timer and a hint
 * when a service is slow or reconnecting; a finished run collapses to a
 * summary ("7 steps · 3m 12s · OpenProject, Memory"). The rows stay one click
 * away.
 *
 * The run comes from the whole message content, not from the assistant-ui
 * range this block happens to be rendered in, so the block does not reshuffle
 * when a fragmented live stream settles into one range (see ToolGroupSlot).
 */
function ActivityBlock({ atEnd, parts }: { atEnd: boolean; parts: ToolPart[] }) {
  const { t } = useI18n()
  const copy = t.assistant.tool
  const activity = copy.activity
  const messageId = useAuiState(s => s.message.id)
  // The turn decides, not this run's own parts: the agent keeps working
  // between two steps (model call, context summary) with nothing pending.
  const turnRunning = useAuiState(selectMessageRunning)
  const toolViewMode = useStore($toolViewMode)
  const runKey = parts[0] ? toolPartDisclosureId(parts[0]) : 'run'
  const disclosureId = `activity:${messageId}:${runKey}`
  const open = useDisclosureOpen(disclosureId, toolViewMode === 'technical')
  const enterRef = useEnterAnimation(turnRunning, `activity:${messageId}:${runKey}`)
  const pendingParts = turnRunning ? parts.filter(part => part.result === undefined) : []
  const live = turnRunning && (atEnd || pendingParts.length > 0)
  const current = live ? pendingParts.at(-1) : undefined
  const lastPart = parts.at(-1)

  const timerKey = current
    ? `activity-step:${messageId}:${toolPartDisclosureId(current)}`
    : `activity-idle:${messageId}:${lastPart ? toolPartDisclosureId(lastPart) : 'none'}`

  const stepElapsed = useElapsedSeconds(live, timerKey)
  const summary = useMemo(() => activitySummary(parts), [parts])
  const phaseLine = useMidTurnPhaseLine()
  // The approval itself renders at the end of the message (AIS-746); the
  // block only says that it waits instead of looking busy.
  const awaitingApproval = useAwaitingApproval(current?.toolName)

  const headline = useMemo(() => {
    if (awaitingApproval) {
      return activity.waitingApproval
    }

    if (current) {
      return `${activity.working} · ${buildToolView(current, '').title}`
    }

    if (live) {
      if (phaseLine) {
        return phaseLine
      }

      return lastPart ? `${activity.working} · ${buildToolView({ ...lastPart, result: lastPart.result ?? {} }, '').title}` : activity.working
    }

    return [activity.stepCount(summary.steps), formatActivityDuration(summary.seconds), summary.areas.slice(0, 3).join(', ')]
      .filter(Boolean)
      .join(' · ')
  }, [activity, awaitingApproval, current, lastPart, live, phaseLine, summary])

  let hint = ''

  if (current && !awaitingApproval) {
    const waited = Math.max(stepElapsed, numberArg(current.args, 'progress_elapsed_s'))
    const label = toolLabel(current.toolName, current.args)
    const server = stringArg(current.args, 'mcp_server')

    if (stringArg(current.args, 'progress_state') === 'reconnecting') {
      hint = activity.reconnecting(server ? serviceDisplayName(server) : label?.place?.name || '')
    } else if (waited >= ACTIVITY_SLOW_S) {
      hint = label?.place ? activity.slow(label.place.name) : activity.slowGeneric
    }
  }

  const status: ToolStatus | undefined = live ? 'running' : summary.failed > 0 ? 'warning' : undefined

  return (
    <div
      className={cn(
        'min-w-0 max-w-full overflow-hidden text-[length:var(--conversation-tool-font-size)] text-(--ui-text-tertiary)',
        open && 'rounded-[0.625rem] border border-(--ui-stroke-tertiary)'
      )}
      data-activity-block=""
      data-slot="tool-block"
      ref={enterRef}
    >
      <div
        className={cn(open && 'border-b border-(--ui-stroke-tertiary) px-2 py-1.5')}
        title={open ? activity.hideSteps : activity.showSteps}
      >
        <DisclosureRow
          onToggle={() => setToolDisclosureOpen(disclosureId, !open)}
          open={open}
          trailing={live ? <ActivityTimerText className={TOOL_HEADER_DURATION_CLASS} seconds={stepElapsed} /> : undefined}
        >
          <span aria-live={turnRunning ? 'polite' : undefined} className="flex min-w-0 items-center gap-1.5">
            <ToolGlyph copy={copy} icon={live ? undefined : 'tools'} status={status} />
            <FadeText
              className={cn(
                TOOL_HEADER_TITLE_CLASS,
                live && !awaitingApproval && 'shimmer text-(--ui-text-tertiary)',
                awaitingApproval && 'text-primary'
              )}
            >
              {headline}
            </FadeText>
          </span>
          {hint && <span className={TOOL_HEADER_SUBTITLE_CLASS}>{hint}</span>}
        </DisclosureRow>
      </div>
      {open && (
        <div className="grid min-w-0 max-w-full gap-(--tool-row-gap) overflow-hidden p-1.5">
          {parts.map(part => (
            <ToolEntry key={toolPartDisclosureId(part)} part={part} />
          ))}
        </div>
      )}
    </div>
  )
}

const NO_SUBAGENTS: SubagentProgress[] = []

function stepTitle(toolName: string, pending: boolean): string {
  return buildToolView({ args: {}, result: pending ? undefined : {}, toolName, type: 'tool-call' }, '').title
}

/**
 * One block per child run of a delegate_task call (AIS-796). The headline
 * names the kind of work and the goal; while the child works, the line below
 * says what it is doing; afterwards that line is the child's summary. The
 * whole summary and the child's steps open on demand — the main chat no
 * longer lists the child's tool calls.
 */
function SubagentRunBlock({ icon, part, run }: { icon?: string; part: ToolPart; run: SubagentRunView }) {
  const { t } = useI18n()
  const copy = t.assistant.tool
  const sub = copy.subagent
  const messageId = useAuiState(s => s.message.id)
  const turnRunning = useAuiState(selectMessageRunning)
  const toolViewMode = useStore($toolViewMode)
  const unfinished = run.status === 'running' || run.status === 'queued'
  const live = turnRunning && unfinished
  // A stopped turn leaves a run without result: it did not finish.
  const failed = run.status === 'failed' || (unfinished && !live)
  const disclosureId = `subagent:${messageId}:${toolPartDisclosureId(part)}:${run.index}`
  const open = useDisclosureOpen(disclosureId)
  const enterRef = useEnterAnimation(turnRunning, disclosureId)
  const elapsed = useElapsedSeconds(live, `subagent-run:${disclosureId}`)
  const kind = sub.kinds[run.kind] ?? sub.generic
  const goal = summaryLine(run.goal, 140)
  const headline = goal ? `${kind} · ${goal}` : kind

  const line = live
    ? run.status === 'queued'
      ? sub.queued
      : run.currentTool
        ? stepTitle(run.currentTool, true)
        : sub.working
    : failed
      ? sub.failed
      : summaryLine(run.summary)

  const meta = live
    ? ''
    : [run.stepTotal ? copy.activity.stepCount(run.stepTotal) : '', formatActivityDuration(run.durationSeconds ?? 0)]
        .filter(Boolean)
        .join(' · ')

  const showError = failed && Boolean(run.error) && toolViewMode === 'technical'
  const hasDetails = Boolean(run.summary || run.steps.length || showError)
  const status: ToolStatus | undefined = live ? 'running' : failed ? 'warning' : undefined

  return (
    <div
      className={cn(
        'min-w-0 max-w-full overflow-hidden text-[length:var(--conversation-tool-font-size)] text-(--ui-text-tertiary)',
        open && 'rounded-[0.625rem] border border-(--ui-stroke-tertiary)'
      )}
      data-slot="tool-block"
      data-subagent-run={run.kind || 'generic'}
      ref={enterRef}
    >
      <div
        className={cn(open && 'border-b border-(--ui-stroke-tertiary) px-2 py-1.5')}
        title={hasDetails ? (open ? sub.hideDetails : sub.showDetails) : undefined}
      >
        <DisclosureRow
          onToggle={hasDetails ? () => setToolDisclosureOpen(disclosureId, !open) : undefined}
          open={open}
          trailing={
            live ? (
              <ActivityTimerText className={TOOL_HEADER_DURATION_CLASS} seconds={elapsed} />
            ) : meta ? (
              <span className={TOOL_HEADER_DURATION_CLASS}>{meta}</span>
            ) : undefined
          }
        >
          <span aria-live={live ? 'polite' : undefined} className="flex min-w-0 items-center gap-1.5">
            <ToolGlyph copy={copy} icon={icon} status={status} />
            <FadeText className={cn(TOOL_HEADER_TITLE_CLASS, live && 'shimmer text-(--ui-text-tertiary)')}>
              {headline}
            </FadeText>
          </span>
          {line && (
            <span
              className={cn(TOOL_HEADER_SUBTITLE_CLASS, failed && 'text-amber-700 dark:text-amber-300')}
              data-subagent-line=""
            >
              {line}
            </span>
          )}
        </DisclosureRow>
      </div>
      {open && (
        <div className="grid w-full min-w-0 max-w-full gap-1.5 overflow-hidden p-1.5">
          {run.summary && (
            <div className="max-w-full text-xs leading-relaxed text-(--ui-text-secondary)">
              <p className={TOOL_SECTION_LABEL_CLASS}>{sub.resultLabel}</p>
              <CompactMarkdown
                className={cn(TOOL_SECTION_SURFACE_CLASS, 'max-h-64 wrap-anywhere')}
                text={run.summary}
              />
            </div>
          )}
          {showError && (
            <pre className={cn(TOOL_SECTION_PRE_CLASS, 'whitespace-pre-wrap wrap-anywhere text-destructive/90')}>
              {run.error}
            </pre>
          )}
          {run.steps.length > 0 && (
            <div className="max-w-full text-xs leading-relaxed text-(--ui-text-secondary)">
              <p className={TOOL_SECTION_LABEL_CLASS}>{sub.stepsLabel}</p>
              <ul className="grid gap-0.5 px-2">
                {run.steps.map((step, index) => (
                  <li
                    className={cn(step.failed > 0 && 'text-amber-700 dark:text-amber-300')}
                    key={`${index}:${step.toolName}`}
                  >
                    {stepTitle(step.toolName, false)}
                    {step.count > 1 ? ` ×${step.count}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function SubagentRunBlocks({ part }: { part: ToolPart }) {
  const sessionId = useStore($activeSessionId)
  const bySession = useStore($subagentsBySession)
  const live = (sessionId && bySession[sessionId]) || NO_SUBAGENTS
  const runs = useMemo(() => subagentRuns(part, live), [part, live])
  const icon = useMemo(() => buildToolView({ ...part, result: part.result ?? {} }, '').icon, [part])

  return (
    <div className="grid min-w-0 max-w-full gap-(--tool-row-gap) overflow-hidden" data-subagent-runs="">
      {runs.map(run => (
        <SubagentRunBlock icon={icon} key={`${run.index}:${run.goal}`} part={part} run={run} />
      ))}
    </div>
  )
}

/**
 * Per-tool fallback. Each run of two or more consecutive groupable steps
 * shows as one ActivityBlock, rendered in place of the run's first step; the
 * run's other steps render nothing here (AIS-786). A step on its own and
 * ungrouped tools keep their row; a delegate_task call shows one block per
 * child run (AIS-796).
 */
export const ToolFallback = ({ toolCallId, toolName, args, isError, result }: ToolCallMessagePartProps) => {
  const part: ToolPart = { args, isError, result, toolCallId, toolName, type: 'tool-call' }
  const content = useAuiState(s => s.message.content)
  const runs = useMemo(() => toolStepRuns(content), [content])

  if (toolName === 'delegate_task') {
    return <SubagentRunBlocks part={part} />
  }

  const run = toolCallId ? runs.find(entry => entry.parts.some(step => step.toolCallId === toolCallId)) : undefined

  if (!run || run.parts.length < 2) {
    return <ToolEntry part={part} />
  }

  return run.parts[0]?.toolCallId === toolCallId ? <ActivityBlock atEnd={run.atEnd} parts={run.parts} /> : null
}
