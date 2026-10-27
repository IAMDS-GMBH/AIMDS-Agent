import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearAllPrompts, setApprovalRequest } from '@/store/prompts'
import { $activeSessionId } from '@/store/session'
import { $toolDisclosureStates } from '@/store/tool-view'

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

function message(tools: ToolCallSeed[], running: boolean): ThreadMessage {
  return {
    id: 'assistant-activity-1',
    role: 'assistant',
    content: tools.map(tool => ({
      type: 'tool-call',
      toolCallId: tool.id,
      toolName: tool.name,
      args: tool.args,
      argsText: JSON.stringify(tool.args),
      ...(tool.result ? { result: tool.result } : {})
    })),
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
  // then a terminal step waiting for approval. The block sits above the edit
  // row, so the approval must render at the end of the message.
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
    expect(block.textContent).toContain('Wartet auf deine Freigabe')
    expect(block.querySelector('.shimmer')).toBeNull()
  })
})
