import { describe, expect, it } from 'vitest'

import type { SubagentProgress } from '@/store/subagents'

import { cleanRunSummary, subagentRuns, summaryLine } from './subagent-runs'
import type { ToolPart } from './tool-fallback-model'

// AIS-796: a delegate_task call shows one block per child run. Synthetic data
// only (project DEMO, made-up titles).

const CREATE = 'mcp_AIMDSSuiteMCP_mcp_openproject-pm_create_work_package'

const APPLY_SUMMARY = [
  '| item | status | reference | note |',
  '|---|---|---|---|',
  '| Sample task 01 | ok | DEMO-101 | |',
  '| Sample task 02 | skipped | | needs confirmation |',
  '',
  'ok: 30, failed: 0, skipped: 1',
  '',
  '[Result cut at 6000 chars; the full 9000-char result is in /tmp/x.md — read it with read_file if you need the rest.]'
].join('\n')

function part(args: Record<string, unknown>, result?: Record<string, unknown>): ToolPart {
  return { args, result, toolCallId: 'call-1', toolName: 'delegate_task', type: 'tool-call' }
}

function progress(overrides: Partial<SubagentProgress>): SubagentProgress {
  return {
    filesRead: [],
    filesWritten: [],
    goal: 'Create 31 tasks in DEMO',
    id: 'sa-0-abc',
    parentId: null,
    startedAt: 1,
    status: 'running',
    stream: [],
    taskCount: 1,
    taskIndex: 0,
    updatedAt: 1,
    ...overrides
  }
}

describe('subagentRuns', () => {
  it('turns a finished apply run into one view with merged steps and its summary', () => {
    const trace = [
      { tool: 'mcp_AIMDSSuiteMCP_mcp_openproject-pm_list_projects', status: 'ok' },
      ...Array.from({ length: 31 }, (_, i) => ({ tool: CREATE, status: i === 4 ? 'error' : 'ok' }))
    ]

    const [run] = subagentRuns(
      part(
        { agent: 'apply', goal: 'Create 31 tasks in DEMO' },
        { results: [{ task_index: 0, status: 'completed', summary: APPLY_SUMMARY, duration_seconds: 42, tool_trace: trace }] }
      )
    )

    expect(run).toMatchObject({ durationSeconds: 42, kind: 'apply', status: 'completed', stepTotal: 32 })
    expect(run.steps).toEqual([
      { count: 1, failed: 0, toolName: 'mcp_AIMDSSuiteMCP_mcp_openproject-pm_list_projects' },
      { count: 31, failed: 1, toolName: CREATE }
    ])
    expect(run.summary).not.toContain('Result cut at')
    expect(summaryLine(run.summary)).toBe('ok: 30, failed: 0, skipped: 1')
  })

  it('gives every task of a batch its own run, in order, with its own kind', () => {
    const runs = subagentRuns(
      part(
        {
          tasks: [
            { agent: 'collect', goal: 'Read DEMO time entries for September' },
            { agent: 'sweep', goal: 'Check DEMO tickets without an estimate' }
          ]
        },
        {
          results: [
            { task_index: 1, status: 'failed', error: 'Subagent did not produce a response.' },
            { task_index: 0, status: 'completed', summary: 'Found 12 entries.' }
          ]
        }
      )
    )

    expect(runs.map(run => [run.kind, run.status])).toEqual([
      ['collect', 'completed'],
      ['sweep', 'failed']
    ])
    expect(runs[1].error).toBe('Subagent did not produce a response.')
  })

  it('fills a running run from the live subagent store', () => {
    const live = [
      progress({ currentTool: CREATE, startedAt: 5, toolCount: 7 }),
      progress({ goal: 'Another goal', id: 'sa-0-other' })
    ]

    const [run] = subagentRuns(part({ agent: 'apply', goal: 'Create 31 tasks in DEMO' }), live)

    expect(run).toMatchObject({ currentTool: CREATE, status: 'running', stepTotal: 7 })
    expect(subagentRuns(part({ goal: 'Create 31 tasks in DEMO' }), [progress({ status: 'queued' })])[0].status).toBe(
      'queued'
    )
  })

  it('reads a failed call without per-task results as failed', () => {
    const [run] = subagentRuns(part({ agent: 'apply', goal: 'x' }, { error: 'unknown agent' }))

    expect(run.status).toBe('failed')
    expect(run.error).toBe('unknown agent')
  })
})

describe('summary lines', () => {
  it('drops notes meant for the calling model and markdown chrome', () => {
    expect(cleanRunSummary('Done.\n\n[NOTE: subagent modified files the parent previously read — re-read: a]')).toBe(
      'Done.'
    )
    expect(summaryLine('## Result\n- **3 tickets** updated')).toBe('Result')
    expect(summaryLine('x'.repeat(300), 20)).toHaveLength(20)
  })
})
