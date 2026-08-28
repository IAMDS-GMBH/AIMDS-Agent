import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TodoItem } from '@/types/hermes'

const getTodos = vi.fn()
const toggleTodo = vi.fn()

vi.mock('@/hermes', () => ({
  getTodos: (includeDone?: boolean) => getTodos(includeDone),
  toggleTodo: (todoId: string, done: boolean) => toggleTodo(todoId, done)
}))

function ts(value: string) {
  return Math.floor(new Date(value).getTime() / 1000)
}

function todo(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    content: 'Write desktop tests',
    created_at: ts('2026-01-05T12:00:00Z'),
    id: 'todo-1',
    status: 'pending',
    updated_at: ts('2026-01-05T12:00:00Z'),
    ...overrides
  }
}

async function renderTodosView() {
  const { TodosView } = await import('./index')

  return render(<TodosView />)
}

beforeEach(() => {
  getTodos.mockResolvedValue({ todos: [todo()] })
  toggleTodo.mockResolvedValue(todo({ status: 'completed' }))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TodosView', () => {
  it('shows loading state before todos are loaded', async () => {
    let resolveTodos: ((value: { todos: TodoItem[] }) => void) | undefined
    getTodos.mockReturnValueOnce(
      new Promise<{ todos: TodoItem[] }>(resolve => {
        resolveTodos = resolve
      })
    )

    await renderTodosView()
    expect(screen.getByRole('status', { name: 'Loading todos' })).toBeTruthy()

    resolveTodos?.({ todos: [todo()] })
    await screen.findByText('Write desktop tests')
  })

  it('shows empty state when api returns no todos', async () => {
    getTodos.mockResolvedValueOnce({ todos: [] })

    await renderTodosView()
    expect(await screen.findByText('No todos yet.')).toBeTruthy()
  })

  it('groups by day by default and can toggle to week grouping', async () => {
    getTodos.mockResolvedValueOnce({
      todos: [
        todo({ id: 'todo-1', created_at: ts('2026-01-05T12:00:00Z') }),
        todo({ id: 'todo-2', created_at: ts('2026-01-07T12:00:00Z'), content: 'Review tests' })
      ]
    })

    const { container } = await renderTodosView()
    await screen.findByText('Write desktop tests')
    await screen.findByText('Review tests')

    expect(container.querySelectorAll('h2')).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Week' }))
    expect(container.querySelectorAll('h2')).toHaveLength(1)
  })

  it('toggles todo checkbox via update api path', async () => {
    await renderTodosView()
    const checkbox = await screen.findByRole('checkbox')
    fireEvent.click(checkbox)

    await waitFor(() => expect(toggleTodo).toHaveBeenCalledWith('todo-1', true))
    await waitFor(() => expect(getTodos).toHaveBeenCalledTimes(2))
  })
})
