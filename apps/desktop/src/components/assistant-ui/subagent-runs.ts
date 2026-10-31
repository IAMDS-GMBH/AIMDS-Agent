import type { SubagentProgress } from '@/store/subagents'

import type { ToolPart } from './tool-fallback-model'

/**
 * One delegate_task call, cut into its child runs (AIS-796). Each child gets
 * one block in the thread: the kind of work and the goal, live what it is
 * doing, afterwards its summary — the child's steps stay one click away. The
 * finished state comes from the tool result (it survives a reload); while the
 * call runs, the live subagent store fills in status and the current step.
 */

export type SubagentRunStatus = 'completed' | 'failed' | 'queued' | 'running'

export interface SubagentRunStep {
  count: number
  failed: number
  toolName: string
}

export interface SubagentRunView {
  currentTool?: string
  durationSeconds?: number
  error: string
  goal: string
  index: number
  /** Agent definition name ("apply", "collect" …); empty for a generic run. */
  kind: string
  status: SubagentRunStatus
  /** Consecutive steps of the same tool merged ("×31"). */
  steps: SubagentRunStep[]
  stepTotal: number
  summary: string
}

const FAILED = new Set(['error', 'failed', 'interrupted', 'timeout'])

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)

      return isRecord(parsed) ? parsed : {}
    } catch {
      return {}
    }
  }

  return isRecord(value) ? value : {}
}

const str = (value: unknown) => (typeof value === 'string' ? value : '')
const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : undefined)

function stepsFromTrace(trace: unknown): SubagentRunStep[] {
  const steps: SubagentRunStep[] = []

  for (const raw of Array.isArray(trace) ? trace : []) {
    const entry = asRecord(raw)
    const toolName = str(entry.tool)

    if (!toolName) {
      continue
    }

    const failed = entry.status === 'error' ? 1 : 0
    const last = steps.at(-1)

    if (last?.toolName === toolName) {
      last.count += 1
      last.failed += failed
    } else {
      steps.push({ count: 1, failed, toolName })
    }
  }

  return steps
}

function liveProgress(live: readonly SubagentProgress[], index: number, goal: string): SubagentProgress | undefined {
  let match: SubagentProgress | undefined

  for (const item of live) {
    if (item.parentId || item.taskIndex !== index || item.goal !== goal) {
      continue
    }

    if (!match || item.startedAt >= match.startedAt) {
      match = item
    }
  }

  return match
}

// Notes the tool result carries for the calling model, not for the reader.
const MODEL_NOTE = /\n*\[(?:Result cut at|NOTE: subagent)[^\]]*\]\s*$/

export function cleanRunSummary(text: string): string {
  return text.replace(MODEL_NOTE, '').trim()
}

const isTableLine = (line: string) => /^\s*\|/.test(line) || /^\s*[-:| ]+$/.test(line)

/** The line a collapsed block shows: the first plain line of the summary — a
 *  table's closing count line rather than its header row. */
export function summaryLine(text: string, max = 180): string {
  const line =
    cleanRunSummary(text)
      .split('\n')
      .filter(entry => !isTableLine(entry))
      .map(entry =>
        entry
          .replace(/^\s*(?:#+|[-*+]|\d+[.)])\s+/, '')
          .replace(/[*_`]+/g, '')
          .trim()
      )
      .find(Boolean) ?? ''

  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

export function subagentRuns(part: ToolPart, live: readonly SubagentProgress[] = []): SubagentRunView[] {
  const args = asRecord(part.args)
  const rawTasks = Array.isArray(args.tasks) ? args.tasks.map(asRecord) : []
  const tasks = rawTasks.length ? rawTasks : [args]
  const result = asRecord(part.result)
  const entries = Array.isArray(result.results) ? result.results.map(asRecord) : []
  const finished = part.result !== undefined
  const callError = str(result.error)

  return tasks.map((task, index) => {
    const goal = str(task.goal) || str(args.goal)
    const entry = entries.find(item => item.task_index === index) ?? {}
    const progress = liveProgress(live, index, goal)
    const steps = stepsFromTrace(entry.tool_trace)
    const entryStatus = str(entry.status)

    let status: SubagentRunStatus

    if (entryStatus) {
      status = FAILED.has(entryStatus) ? 'failed' : 'completed'
    } else if (finished) {
      status = callError || part.isError ? 'failed' : 'completed'
    } else {
      status = progress?.status === 'queued' ? 'queued' : 'running'
    }

    return {
      currentTool: status === 'running' ? progress?.currentTool : undefined,
      durationSeconds: num(entry.duration_seconds) ?? progress?.durationSeconds,
      error: str(entry.error) || callError,
      goal,
      index,
      kind: str(task.agent) || str(args.agent),
      status,
      steps,
      stepTotal: steps.reduce((total, step) => total + step.count, 0) || progress?.toolCount || 0,
      summary: cleanRunSummary(str(entry.summary) || (finished ? '' : (progress?.summary ?? '')))
    }
  })
}
