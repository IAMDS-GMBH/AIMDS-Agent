import { useCallback, useEffect, useMemo, useState } from 'react'

import { useRefreshHotkey } from '@/app/hooks/use-refresh-hotkey'
import { PageLoader } from '@/components/page-loader'
import { Checkbox } from '@/components/ui/checkbox'
import { getTodos, type TodoItem, toggleTodo } from '@/hermes'
import { cn } from '@/lib/utils'

type GroupMode = 'day' | 'week'

function dayKey(ts: number): string {
  const d = new Date(ts * 1000)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')

  return `${y}-${m}-${day}`
}

function weekKey(ts: number): string {
  const d = new Date(ts * 1000)
  const local = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const day = local.getDay()
  const diff = day === 0 ? -6 : 1 - day
  local.setDate(local.getDate() + diff)

  return dayKey(local.getTime() / 1000)
}

function sectionLabel(mode: GroupMode, key: string): string {
  const start = new Date(`${key}T00:00:00`)

  if (mode === 'day') {
    return start.toLocaleDateString(undefined, { day: 'numeric', month: 'short', weekday: 'short', year: 'numeric' })
  }

  const end = new Date(start)
  end.setDate(end.getDate() + 6)

  return `${start.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} – ${end.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`
}

export function TodosView() {
  const [items, setItems] = useState<null | TodoItem[]>(null)
  const [groupMode, setGroupMode] = useState<GroupMode>('day')
  const [busyIds, setBusyIds] = useState<Record<string, boolean>>({})

  const refresh = useCallback(async () => {
    const result = await getTodos(true)
    const ordered = [...(result.todos ?? [])].sort((a, b) => a.created_at - b.created_at)
    setItems(ordered)
  }, [])

  useRefreshHotkey(() => void refresh())

  useEffect(() => {
    void refresh()
  }, [refresh])

  const grouped = useMemo(() => {
    const list = items ?? []
    const keyOf = groupMode === 'day' ? dayKey : weekKey
    const map = new Map<string, TodoItem[]>()

    for (const item of list) {
      const key = keyOf(item.created_at)
      const section = map.get(key) ?? []
      section.push(item)
      map.set(key, section)
    }

    return [...map.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  }, [groupMode, items])

  const onToggle = useCallback(async (todo: TodoItem, checked: boolean) => {
    setBusyIds(current => ({ ...current, [todo.id]: true }))

    try {
      await toggleTodo(todo.id, checked)
      await refresh()
    } finally {
      setBusyIds(current => {
        const next = { ...current }
        delete next[todo.id]

        return next
      })
    }
  }, [refresh])

  if (items == null) {
    return <PageLoader label="Loading todos" />
  }

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden px-4 pt-[calc(var(--titlebar-height)+0.75rem)] pb-4">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold text-foreground">Todos</h1>
          <p className="text-sm text-muted-foreground">Chronological tasks grouped by day or week.</p>
        </div>
        <div className="inline-flex rounded-md border border-border/60 p-0.5">
          <button
            className={cn(
              'rounded px-2.5 py-1 text-xs font-medium',
              groupMode === 'day' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
            onClick={() => setGroupMode('day')}
            type="button"
          >
            Day
          </button>
          <button
            className={cn(
              'rounded px-2.5 py-1 text-xs font-medium',
              groupMode === 'week' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
            onClick={() => setGroupMode('week')}
            type="button"
          >
            Week
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {grouped.length === 0 ? (
          <div className="rounded-md border border-dashed border-border/70 px-3 py-6 text-center text-sm text-muted-foreground">
            No todos yet.
          </div>
        ) : (
          <div className="space-y-4">
            {grouped.map(([key, sectionItems]) => (
              <section key={key}>
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {sectionLabel(groupMode, key)}
                </h2>
                <ul className="space-y-1.5">
                  {sectionItems.map(todo => {
                    const checked = todo.status === 'completed'
                    const busy = Boolean(busyIds[todo.id])

                    return (
                      <li
                        className={cn(
                          'flex items-center gap-3 rounded-md border border-border/60 bg-card px-3 py-2',
                          checked && 'opacity-70'
                        )}
                        key={todo.id}
                      >
                        <Checkbox
                          checked={checked}
                          disabled={busy}
                          onCheckedChange={state => void onToggle(todo, state === true)}
                        />
                        <span className={cn('min-w-0 flex-1 text-sm text-foreground', checked && 'line-through')}>
                          {todo.content}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
