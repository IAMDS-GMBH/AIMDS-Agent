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
import { useEnterAnimation } from '@/lib/use-enter-animation'
import { cn } from '@/lib/utils'
import { $toolInlineDiffs } from '@/store/tool-diffs'
import { $toolDisclosureOpen, $toolViewMode, setToolDisclosureOpen } from '@/store/tool-view'

import { PendingToolApproval } from './tool-approval'
import {
  activitySummary,
  buildToolView,
  cleanVisibleText,
  formatActivityDuration,
  groupableToolParts,
  inlineDiffFromResult,
  isPreviewableTarget,
  looksRedundant,
  type SearchResultRow,
  selectMessageRunning,
  stripInlineDiffChrome,
  toolCopyPayload,
  type ToolPart,
  toolPartDisclosureId,
  type ToolStatus
} from './tool-fallback-model'

// `true` when a ToolEntry is rendered inside an embedding wrapper that owns
// the per-row chrome (timer / preview). The flat ToolGroupSlot sets this
// false, so every row currently owns its own chrome; kept as a seam for any
// future embedding surface.
const ToolEmbedContext = createContext(false)

// Shared header chrome for tool rows. Both the single-tool DisclosureRow
// and the multi-tool group header pass through these constants so a
// "Patch" row and a "Tool actions · 2 steps" row are visually identical.
const TOOL_HEADER_TITLE_CLASS =
  'text-[length:var(--conversation-tool-font-size)] font-medium leading-(--conversation-line-height) text-(--ui-text-secondary)'

const TOOL_HEADER_DURATION_CLASS = 'shrink-0 text-[0.625rem] tabular-nums text-(--ui-text-tertiary)'

const TOOL_HEADER_SUBTITLE_CLASS =
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
      {isPending && <PendingToolApproval part={part} />}
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

/**
 * One block for all tool steps of a message (AIS-744). While the turn runs it
 * says in one line what is happening right now, with a timer and a hint when
 * a service is slow or reconnecting; afterwards it collapses to a summary
 * ("7 steps · 3m 12s · OpenProject, Memory"). The rows stay one click away.
 *
 * The parts come from the whole message content, not from the assistant-ui
 * range this block happens to be rendered in, so the block does not reshuffle
 * when a fragmented live stream settles into one range (see ToolGroupSlot).
 */
function ActivityBlock({ parts }: { parts: ToolPart[] }) {
  const { t } = useI18n()
  const copy = t.assistant.tool
  const activity = copy.activity
  const messageId = useAuiState(s => s.message.id)
  const messageRunning = useAuiState(selectMessageRunning)
  const toolViewMode = useStore($toolViewMode)
  const disclosureId = `activity:${messageId}`
  const open = useDisclosureOpen(disclosureId, toolViewMode === 'technical')
  const enterRef = useEnterAnimation(messageRunning, `activity:${messageId}`)
  const pendingParts = messageRunning ? parts.filter(part => part.result === undefined) : []
  const current = pendingParts.at(-1)
  const currentKey = current ? toolPartDisclosureId(current) : 'idle'
  const stepElapsed = useElapsedSeconds(Boolean(current), `activity-step:${messageId}:${currentKey}`)
  const summary = useMemo(() => activitySummary(parts), [parts])
  const lastPart = parts.at(-1)

  const headline = useMemo(() => {
    if (current) {
      return buildToolView(current, '').title
    }

    if (messageRunning) {
      return lastPart ? `${activity.working} · ${buildToolView({ ...lastPart, result: lastPart.result ?? {} }, '').title}` : activity.working
    }

    return [activity.stepCount(summary.steps), formatActivityDuration(summary.seconds), summary.areas.slice(0, 3).join(', ')]
      .filter(Boolean)
      .join(' · ')
  }, [activity, current, lastPart, messageRunning, summary])

  let hint = ''

  if (current) {
    const waited = Math.max(stepElapsed, numberArg(current.args, 'progress_elapsed_s'))
    const label = toolLabel(current.toolName, current.args)
    const server = stringArg(current.args, 'mcp_server')

    if (stringArg(current.args, 'progress_state') === 'reconnecting') {
      hint = activity.reconnecting(server ? serviceDisplayName(server) : label?.place?.name || '')
    } else if (waited >= ACTIVITY_SLOW_S) {
      hint = label?.place ? activity.slow(label.place.name) : activity.slowGeneric
    }
  }

  const status: ToolStatus | undefined = current ? 'running' : summary.failed > 0 ? 'warning' : undefined

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
          trailing={current ? <ActivityTimerText className={TOOL_HEADER_DURATION_CLASS} seconds={stepElapsed} /> : undefined}
        >
          <span aria-live={messageRunning ? 'polite' : undefined} className="flex min-w-0 items-center gap-1.5">
            <ToolGlyph copy={copy} icon={current ? undefined : 'tools'} status={status} />
            <FadeText className={cn(TOOL_HEADER_TITLE_CLASS, current && 'shimmer text-(--ui-text-tertiary)')}>
              {headline}
            </FadeText>
          </span>
          {hint && <span className={TOOL_HEADER_SUBTITLE_CLASS}>{hint}</span>}
        </DisclosureRow>
      </div>
      {pendingParts.map(part => (
        <PendingToolApproval key={toolPartDisclosureId(part)} part={part} />
      ))}
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

/**
 * Per-tool fallback. A message with two or more groupable tool steps shows
 * them as one ActivityBlock, rendered in place of the first step; the other
 * steps render nothing here. Single steps and ungrouped tools keep their row.
 */
export const ToolFallback = ({ toolCallId, toolName, args, isError, result }: ToolCallMessagePartProps) => {
  const part: ToolPart = { args, isError, result, toolCallId, toolName, type: 'tool-call' }
  const content = useAuiState(s => s.message.content)
  const group = useMemo(() => groupableToolParts(content), [content])
  const inGroup = group.length > 1 && group.some(entry => entry.toolCallId === toolCallId)

  if (!inGroup) {
    return <ToolEntry part={part} />
  }

  return group[0]?.toolCallId === toolCallId ? <ActivityBlock parts={group} /> : null
}
