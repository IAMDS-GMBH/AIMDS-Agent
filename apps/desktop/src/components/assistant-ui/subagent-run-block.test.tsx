import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearAllPrompts } from '@/store/prompts'
import { $activeSessionId } from '@/store/session'
import { $subagentsBySession } from '@/store/subagents'
import { $toolDisclosureStates } from '@/store/tool-view'

import { Thread } from './thread'

// AIS-796: a delegate_task call renders one block per child run — kind and
// goal, a live line while it works, the child's summary afterwards — and the
// child's steps only on demand. Synthetic data only (project DEMO).

const createdAt = new Date('2026-10-31T00:00:00.000Z')

class TestResizeObserver {
  observe() {}

  unobserve() {}

  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
  window.setTimeout(() => callback(performance.now()), 0)
)
vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))

Element.prototype.scrollTo = function scrollTo() {}

Element.prototype.animate = function animate() {
  return {
    cancel: () => {},
    finished: Promise.resolve()
  } as unknown as Animation
}

for (const [prop, clientProp, fallback] of [
  ['offsetWidth', 'clientWidth', 800],
  ['offsetHeight', 'clientHeight', 600]
] as const) {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop)

  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      return previous?.get?.call(this) || (this as HTMLElement)[clientProp] || fallback
    }
  })
}

const CREATE = 'mcp_AIMDSSuiteMCP_mcp_openproject-pm_create_work_package'
const GOAL = 'Create the 31 approved tasks in project DEMO'

const SUMMARY = [
  '| item | status | reference | note |',
  '|---|---|---|---|',
  '| Sample task 01 | ok | DEMO-101 | |',
  '',
  'ok: 31, failed: 0, skipped: 0'
].join('\n')

function message(result: Record<string, unknown> | undefined, running: boolean): ThreadMessage {
  const args = { agent: 'apply', goal: GOAL, context: 'Sample task 01 … Sample task 31' }

  return {
    id: 'assistant-subagent-1',
    role: 'assistant',
    content: [
      { type: 'text', text: 'Ich lege die freigegebenen Aufgaben an.' },
      {
        type: 'tool-call',
        toolCallId: 'delegate-1',
        toolName: 'delegate_task',
        args,
        argsText: JSON.stringify(args),
        ...(result ? { result } : {})
      }
    ],
    status: running ? { type: 'running' } : { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} }
  } as ThreadMessage
}

function Harness({ value }: { value: ThreadMessage }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [value],
    isRunning: value.status?.type === 'running',
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

beforeEach(() => {
  clearAllPrompts()
  $activeSessionId.set('sess-1')
  $toolDisclosureStates.set({})
})

afterEach(() => {
  cleanup()
  $activeSessionId.set(null)
  $subagentsBySession.set({})
})

describe('subagent run block', () => {
  it('shows the running child with its kind, goal and current step', async () => {
    $subagentsBySession.set({
      'sess-1': [
        {
          currentTool: CREATE,
          filesRead: [],
          filesWritten: [],
          goal: GOAL,
          id: 'sa-0-demo',
          parentId: null,
          startedAt: 1,
          status: 'running',
          stream: [],
          taskCount: 1,
          taskIndex: 0,
          toolCount: 9,
          updatedAt: 1
        }
      ]
    })

    const { container } = render(<Harness value={message(undefined, true)} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-subagent-run]')).toHaveLength(1)
    })

    const block = container.querySelector('[data-subagent-run]')!

    expect(block.textContent).toContain(`Änderungen umsetzen · ${GOAL}`)
    expect(block.querySelector('.shimmer')).toBeTruthy()
    expect(block.querySelector('[data-subagent-line]')?.textContent).toMatch(/OpenProject/)
  })

  it('collapses a finished child to its summary and opens result and steps on click', async () => {
    const result = {
      results: [
        {
          task_index: 0,
          status: 'completed',
          summary: SUMMARY,
          duration_seconds: 75,
          tool_trace: Array.from({ length: 31 }, () => ({ tool: CREATE, status: 'ok' }))
        }
      ]
    }

    const { container, getByRole } = render(<Harness value={message(result, false)} />)

    await waitFor(() => {
      expect(container.querySelector('[data-subagent-line]')?.textContent).toBe('ok: 31, failed: 0, skipped: 0')
    })

    const block = container.querySelector('[data-subagent-run]')!

    expect(block.textContent).toContain('31 Schritte')
    expect(block.textContent).not.toContain('DEMO-101')
    expect(block.querySelector('.shimmer')).toBeNull()

    fireEvent.click(getByRole('button', { name: /Änderungen umsetzen/ }))

    await waitFor(() => {
      expect(container.textContent).toContain('DEMO-101')
    })
    expect(container.textContent).toContain('×31')
  })

  it('says plainly when a child did not finish', async () => {
    const result = { results: [{ task_index: 0, status: 'timeout', error: 'Subagent timed out after 600s' }] }
    const { container } = render(<Harness value={message(result, false)} />)

    await waitFor(() => {
      expect(container.querySelector('[data-subagent-line]')?.textContent).toBe(
        'Diese Teilaufgabe ist nicht fertig geworden.'
      )
    })
    expect(container.textContent).not.toContain('timed out')
  })
})
