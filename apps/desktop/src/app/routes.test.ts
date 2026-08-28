import { describe, expect, it } from 'vitest'

import { APP_ROUTES, appViewForPath, TODOS_ROUTE } from './routes'

describe('app routes', () => {
  it('registers todos route metadata', () => {
    expect(APP_ROUTES).toContainEqual({ id: 'todos', path: TODOS_ROUTE, view: 'todos' })
  })

  it('maps /todos path to todos view', () => {
    expect(appViewForPath(TODOS_ROUTE)).toBe('todos')
  })
})

