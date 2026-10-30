import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearAllPrompts, setApprovalRequest } from '@/store/prompts'
import { $activeSessionId } from '@/store/session'
import { $toolDisclosureStates } from '@/store/tool-view'
import { $turnPhaseBySession } from '@/store/turn-phase'

import { Thread } from './thread'

// AIS-744: a turn's tool steps render as ONE activity block — a live line
// while running, a collapsed summary afterwards — instead of a list of raw
// rows like "Mcp AIMDSSuiteMCP Mcp Memory Memory Read".

const createdAt = new Date('2026-06-03T00:00:00.000Z')

const resizeObservers = new Set<TestResizeObserver>()

class TestResizeObserver {
  private target: Element | null = null

  constructor(private readonly callback: ResizeObserverCallback) {
    resizeObservers.add(this)
  }

  observe(target: Element) {
    this.target = target
  }

  unobserve() {}

  disconnect() {
    resizeObservers.delete(this)
  }
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

function stubOffsetDimension(
  prop: 'offsetHeight' | 'offsetWidth',
  clientProp: 'clientHeight' | 'clientWidth',
  fallback: number
) {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop)

  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      return previous?.get?.call(this) || (this as HTMLElement)[clientProp] || fallback
    }
  })
}

stubOffsetDimension('offsetWidth', 'clientWidth', 800)
stubOffsetDimension('offsetHeight', 'clientHeight', 600)

type ToolCallSeed = { args: Record<string, unknown>; id: string; name: string; result?: Record<string, unknown> }

type Seed = ToolCallSeed | string

function message(seeds: Seed[], running: boolean): ThreadMessage {
  return {
    id: 'assistant-activity-1',
    role: 'assistant',
    content: seeds.map(seed =>
      typeof seed === 'string'
        ? { type: 'text', text: seed }
        : {
            type: 'tool-call',
            toolCallId: seed.id,
            toolName: seed.name,
            args: seed.args,
            argsText: JSON.stringify(seed.args),
            ...(seed.result ? { result: seed.result } : {})
          }
    ),
    status: running ? { type: 'running' } : { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {}
    }
  } as ThreadMessage
}

const SESSION_TOOLS: ToolCallSeed[] = [
  {
    id: 'm-1',
    name: 'mcp_AIMDSSuiteMCP_mcp_memory_memory_read',
    args: { slug: 'some-note' },
    result: { duration_s: 0.27 }
  },
  {
    id: 'm-2',
    name: 'mcp_AIMDSSuiteMCP_mcp_openproject_pm_search_work_packages',
    args: { query: 'Urlaub 2026' },
    result: { duration_s: 1.4 }
  },
  {
    id: 'm-3',
    name: 'workdays',
    args: { action: 'absences' },
    result: { duration_s: 0.01 }
  }
]

function Harness({ busy, value }: { busy?: boolean; value: ThreadMessage }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [value],
    isRunning: busy ?? value.status?.type === 'running',
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
})

describe('activity block', () => {
  it('shows one live line for the running step instead of raw rows', async () => {
    const running = [
      ...SESSION_TOOLS.slice(0, 2),
      { id: 'm-4', name: 'mcp_AIMDSSuiteMCP_mcp_memory_memory_search', args: { query: 'Urlaubsanspruch' } }
    ]

    const { container } = render(<Harness value={message(running, true)} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-activity-block]')).toHaveLength(1)
    })
    expect(container.textContent).toContain('Suche Notizen im Gedächtnis: „Urlaubsanspruch“')
    expect(container.textContent).not.toContain('Tickets in OpenProject gesucht')
    expect(container.textContent).not.toMatch(/Mcp AIMDSSuiteMCP/)
  })

  it('collapses a finished turn to a summary and opens the steps on click', async () => {
    const { container, getByRole } = render(<Harness value={message(SESSION_TOOLS, false)} />)

    await waitFor(() => {
      expect(container.textContent).toContain('3 Schritte')
    })
    expect(container.textContent).toContain('OpenProject')
    expect(container.textContent).toContain('Arbeitszeit')
    expect(container.textContent).not.toContain('Tickets in OpenProject gesucht')

    fireEvent.click(getByRole('button', { name: /3 Schritte/ }))

    await waitFor(() => {
      expect(container.textContent).toContain('Tickets in OpenProject gesucht: „Urlaub 2026“')
    })
  })

  it('keeps a single step as its own row', async () => {
    const { container } = render(<Harness value={message(SESSION_TOOLS.slice(1, 2), false)} />)

    await waitFor(() => {
      expect(container.textContent).toContain('Tickets in OpenProject gesucht: „Urlaub 2026“')
    })
    expect(container.textContent).not.toContain('Schritt')
  })

  // AIS-746 (session 20261027_170015_2d955b): grouped steps, then a file edit,
  // then a terminal step waiting for approval. Since AIS-786 the edit row ends
  // the first run, so the waiting step is its own row at the end; the approval
  // still renders at the end of the message.
  it('shows a pending approval at the end of the message, after later file-edit rows', async () => {
    setApprovalRequest({ command: 'rm -f /tmp/b13.txt', description: 'delete in root path', sessionId: 'sess-1' })

    const turn = [
      ...SESSION_TOOLS.slice(0, 2),
      { id: 'p-1', name: 'patch', args: { path: '/tmp/changelog.md' }, result: { success: true } },
      { id: 't-1', name: 'terminal', args: { command: 'wc -l changelog.md && rm -f /tmp/b13.txt' } }
    ]

    const { container } = render(<Harness value={message(turn, true)} />)

    await waitFor(() => {
      expect(container.querySelector('[data-slot="tool-approval-notice"]')).toBeTruthy()
    })

    const content = container.querySelector('[data-slot="aui_assistant-message-content"]')!
    const notice = container.querySelector('[data-slot="tool-approval-notice"]')!
    const block = container.querySelector('[data-activity-block]')!

    expect(container.querySelectorAll('[data-slot="tool-approval-inline"]')).toHaveLength(1)
    expect(content.lastElementChild?.contains(notice) || content.lastElementChild === notice).toBe(true)
    expect(block.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // The finished run above the edit row is a summary, not a second live line.
    expect(block.textContent).toContain('2 Schritte')
    expect(block.querySelector('.shimmer')).toBeNull()
  })
})

// AIS-786 (session 20261030_111823_c52cc1): interim text, steps, interim text,
// more steps. All steps used to land in one block at the first step, which
// showed its finished summary while the agent kept working below.
describe('activity block per run of steps', () => {
  const firstRun = SESSION_TOOLS

  const laterDone = {
    id: 'b-1',
    name: 'mcp_AIMDSSuiteMCP_mcp_openproject_pm_search_work_packages',
    args: { query: 'Board Spalten' },
    result: { duration_s: 2 }
  }

  const laterRunning = { id: 'b-2', name: 'mcp_AIMDSSuiteMCP_mcp_memory_memory_search', args: { query: 'Board' } }

  const turn: Seed[] = ['Ich schaue mir das an.', ...firstRun, 'Das Board ist groß, ich filtere weiter.', laterDone, laterRunning]

  afterEach(() => {
    $turnPhaseBySession.set({})
  })

  it('renders one block per run, the running one last and live', async () => {
    const { container } = render(<Harness value={message(turn, true)} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-activity-block]')).toHaveLength(2)
    })

    const [done, live] = Array.from(container.querySelectorAll('[data-activity-block]'))
    const content = container.querySelector('[data-slot="aui_assistant-message-content"]')!
    const laterText = Array.from(content.querySelectorAll('*')).find(node => node.textContent === 'Das Board ist groß, ich filtere weiter.')!

    expect(done.textContent).toContain('3 Schritte')
    expect(done.querySelector('.shimmer')).toBeNull()
    expect(live.textContent).toContain('Ich arbeite daran · Suche Notizen im Gedächtnis: „Board“')
    expect(live.querySelector('.shimmer')).toBeTruthy()
    expect(laterText.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(done.compareDocumentPosition(laterText) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  // Between two model calls nothing is pending and the live message may have
  // been settled by a view merge; the busy turn still keeps the tail block live.
  it('stays live from the running turn when the message itself was settled', async () => {
    const steps = [...turn.slice(0, -1), { ...laterRunning, result: { duration_s: 1 } }]
    const { container } = render(<Harness busy value={message(steps, false)} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-activity-block]')).toHaveLength(2)
    })

    const [done, live] = Array.from(container.querySelectorAll('[data-activity-block]'))

    expect(done.textContent).toContain('3 Schritte')
    expect(live.textContent).toContain('Ich arbeite daran · ')
    expect(live.querySelector('.shimmer')).toBeTruthy()
  })

  it('says that it summarizes the conversation while a mid-turn compression runs', async () => {
    const steps = [...turn.slice(0, -1), { ...laterRunning, result: { duration_s: 1 } }]

    $turnPhaseBySession.set({ 'sess-1': { messages: 40, name: 'compressing', since: Date.now() } })

    const { container } = render(<Harness value={message(steps, true)} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-activity-block]')).toHaveLength(2)
    })
    expect(container.querySelectorAll('[data-activity-block]')[1].textContent).toContain(
      'Ich fasse den bisherigen Verlauf zusammen'
    )
    expect(container.querySelectorAll('[data-activity-block]')[0].textContent).toContain('3 Schritte')
  })
})
