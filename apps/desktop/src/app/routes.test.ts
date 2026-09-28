import { describe, expect, it } from 'vitest'

import { APP_ROUTES, appViewForPath, routeSessionId, TODOS_ROUTE } from './routes'

describe('app routes', () => {
  it('no longer registers the retired messaging page', () => {
    expect(APP_ROUTES.map(route => route.path)).not.toContain('/messaging')
    expect(appViewForPath('/messaging')).toBe('chat')
    expect(routeSessionId('/messaging')).toBeNull()
  })

  it('registers todos route metadata', () => {
    expect(APP_ROUTES).toContainEqual({ id: 'todos', path: TODOS_ROUTE, view: 'todos' })
  })

  it('maps /todos path to todos view', () => {
    expect(appViewForPath(TODOS_ROUTE)).toBe('todos')
  })
})
