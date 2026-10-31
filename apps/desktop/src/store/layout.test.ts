import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// AIS-794: the sidebar keeps its section open states and pane widths across
// launches, in localStorage like the other layout keys.
describe('sidebar layout persistence (AIS-794)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  afterEach(() => {
    window.localStorage.clear()
  })

  it('opens Favoriten, sessions and Im Hintergrund by default', async () => {
    const layout = await import('./layout')

    expect(layout.$sidebarPinsOpen.get()).toBe(true)
    expect(layout.$sidebarRecentsOpen.get()).toBe(true)
    expect(layout.$sidebarBackgroundOpen.get()).toBe(true)
  })

  it('stores a collapsed section and reads it back on the next launch', async () => {
    const first = await import('./layout')

    first.setSidebarPinsOpen(false)
    first.setSidebarRecentsOpen(false)
    first.setSidebarBackgroundOpen(false)

    expect(window.localStorage.getItem('hermes.desktop.sidebarPinsOpen')).toBe('false')
    expect(window.localStorage.getItem('hermes.desktop.sidebarRecentsOpen')).toBe('false')
    expect(window.localStorage.getItem('hermes.desktop.sidebarBackgroundOpen')).toBe('false')

    vi.resetModules()
    const next = await import('./layout')

    expect(next.$sidebarPinsOpen.get()).toBe(false)
    expect(next.$sidebarRecentsOpen.get()).toBe(false)
    expect(next.$sidebarBackgroundOpen.get()).toBe(false)
  })

  it('does not inherit the retired automations-section state', async () => {
    window.localStorage.setItem('hermes.desktop.sidebarCronOpen', 'false')

    const layout = await import('./layout')

    expect(layout.$sidebarBackgroundOpen.get()).toBe(true)
    expect(window.localStorage.getItem('hermes.desktop.sidebarCronOpen')).toBeNull()
  })

  it('keeps the sessions sidebar width across launches', async () => {
    const first = await import('./layout')

    first.setSidebarWidth(300)

    vi.resetModules()
    const next = await import('./layout')

    expect(next.$sidebarWidth.get()).toBe(300)
  })
})
