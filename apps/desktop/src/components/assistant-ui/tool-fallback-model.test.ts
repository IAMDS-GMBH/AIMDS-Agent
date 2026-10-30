import { describe, expect, it } from 'vitest'

import {
  buildToolView,
  liveStepLineAtEnd,
  selectMessageRunning,
  type ToolPart,
  toolStepRuns
} from './tool-fallback-model'

const part = (overrides: Partial<ToolPart>): ToolPart => ({
  args: {},
  isError: false,
  result: {},
  toolCallId: 'call_1',
  toolName: 'vision_analyze',
  type: 'tool-call',
  ...overrides
})

describe('buildToolView image handling', () => {
  // vision_analyze reports the input image as a local path; an <img> pointed at
  // a bare path resolves against the renderer origin and 404s, so we render the
  // tool codicon instead of a broken image.
  it('drops bare filesystem paths', () => {
    expect(buildToolView(part({ args: { path: '/Users/me/shot.png' } }), '').imageUrl).toBe('')
    expect(buildToolView(part({ result: { image_path: '/tmp/out.jpg' } }), '').imageUrl).toBe('')
  })

  it('keeps fetchable data URLs', () => {
    const dataUrl = 'data:image/png;base64,AAAA'

    expect(buildToolView(part({ result: { image_url: dataUrl } }), '').imageUrl).toBe(dataUrl)
  })

  it('keeps remote http(s) image URLs', () => {
    const url = 'https://example.com/pic.webp'

    expect(buildToolView(part({ result: { url } }), '').imageUrl).toBe(url)
  })
})

describe('buildToolView terminal exit-code status', () => {
  const terminal = (result: Record<string, unknown>) =>
    buildToolView(part({ result, toolName: 'terminal' }), '')

  // A non-zero exit code with real output is not a failure (grep no-match,
  // diff differences, piped commands surfacing the last stage's code, etc.) —
  // it should render as success so the card isn't painted red.
  it('treats non-zero exit with output as success', () => {
    expect(terminal({ exit_code: 7, output: 'node ... 5174 (LISTEN)' }).status).toBe('success')
    expect(terminal({ exit_code: 1, stdout: 'partial results' }).status).toBe('success')
  })

  // No output + non-zero exit is a genuine failure worth flagging.
  it('treats non-zero exit with no output as error', () => {
    expect(terminal({ exit_code: 127, output: '' }).status).toBe('error')
    expect(terminal({ exit_code: 1 }).status).toBe('error')
  })

  it('treats zero exit as success', () => {
    expect(terminal({ exit_code: 0, output: 'done' }).status).toBe('success')
  })

  // Explicit error signals still win regardless of output presence.
  it('keeps explicit error signals red even with output', () => {
    expect(terminal({ error: 'boom', exit_code: 0, output: 'partial' }).status).toBe('error')
    expect(buildToolView(part({ isError: true, result: { output: 'x' }, toolName: 'terminal' }), '').status).toBe(
      'error'
    )
  })
})

describe('buildToolView MCP memory-save title', () => {
  it('appends + Local when local mirror metadata is present', () => {
    const view = buildToolView(
      part({
        toolName: 'mcp_IAMDS_mcp_memory_memory_save',
        args: { __local_mirror: true },
        result: { success: true }
      }),
      ''
    )

    expect(view.title).toBe('mcp_IAMDS_mcp_memory_memory_save + Local')
  })
})

const step = (toolName: string, done = true, id = toolName) => ({
  type: 'tool-call',
  toolCallId: id,
  toolName,
  ...(done ? { result: {} } : {})
})

const text = (value: string) => ({ type: 'text', text: value })

describe('toolStepRuns (AIS-786)', () => {
  const ids = (content: unknown[]) => toolStepRuns(content).map(run => [run.parts.map(part => part.toolCallId), run.atEnd])

  it('cuts the steps at every visible text, so later steps do not join the first block', () => {
    // Session 20261030_111823_c52cc1: interim text, steps, interim text, more steps.
    const content = [
      text('Ich schaue mir das Board an.'),
      step('tool_search', true, 'a'),
      step('mcp_hub_op_board', true, 'b'),
      text('Das Board ist groß, ich filtere.'),
      step('mcp_hub_op_board', true, 'c'),
      step('sql', true, 'd'),
      text('Noch die Spalten.'),
      step('mcp_hub_op_board', false, 'e')
    ]

    expect(ids(content)).toEqual([
      [['a', 'b'], false],
      [['c', 'd'], false],
      [['e'], true]
    ])
  })

  it('keeps one run across whitespace and reasoning, and ends it at an ungrouped row', () => {
    const content = [
      step('read_file', true, 'a'),
      text('  '),
      { type: 'reasoning', text: 'hmm' },
      step('terminal', true, 'b'),
      step('patch', true, 'p'),
      step('terminal', false, 'c'),
      text('\n')
    ]

    expect(ids(content)).toEqual([
      [['a', 'b'], false],
      [['c'], true]
    ])
  })

  it('cuts the same runs from a fragmented live stream and the settled message', () => {
    const live = [text('Goal'), step('a_tool', true, 'a'), text(''), step('b_tool', true, 'b'), text('Done.')]
    const settled = [text('Goal'), step('a_tool', true, 'a'), step('b_tool', true, 'b'), text('Done.')]

    expect(ids(live)).toEqual(ids(settled))
  })
})

describe('liveStepLineAtEnd (AIS-746/786)', () => {
  it('is false when text or a finished single row ends the message', () => {
    expect(liveStepLineAtEnd([step('terminal'), text('done')])).toBe(false)
    expect(liveStepLineAtEnd([step('terminal')])).toBe(false)
    expect(liveStepLineAtEnd([step('read_file'), step('terminal'), text('Now the changelog')])).toBe(false)
  })

  it('is true for a running step at the end, as a row or in the last block', () => {
    expect(liveStepLineAtEnd([text('Let me check'), step('terminal', false)])).toBe(true)
    expect(liveStepLineAtEnd([step('read_file'), step('patch', false)])).toBe(true)
    expect(liveStepLineAtEnd([text('Goal'), step('read_file'), text(' '), step('terminal', false)])).toBe(true)
    expect(liveStepLineAtEnd([step('read_file'), text('Now the changelog'), step('terminal', false)])).toBe(true)
  })

  it('is true while the last block waits between two steps', () => {
    expect(liveStepLineAtEnd([text('Goal'), step('read_file', true, 'a'), step('terminal', true, 'b')])).toBe(true)
  })
})

describe('selectMessageRunning (AIS-786)', () => {
  const state = (isRunning: boolean, message: { isLast?: boolean; role?: string; status?: { type?: string } }) => ({
    message,
    thread: { isRunning }
  })

  it('follows the running turn even when the live message was settled', () => {
    expect(selectMessageRunning(state(true, { isLast: true, role: 'assistant', status: { type: 'complete' } }))).toBe(true)
    expect(selectMessageRunning(state(true, { isLast: false, role: 'assistant', status: { type: 'running' } }))).toBe(true)
  })

  it('is false for earlier messages and once the turn ended', () => {
    expect(selectMessageRunning(state(true, { isLast: false, role: 'assistant', status: { type: 'complete' } }))).toBe(false)
    expect(selectMessageRunning(state(false, { isLast: true, role: 'assistant', status: { type: 'running' } }))).toBe(false)
  })
})
