import { cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { $floatingOverlay, loadFloatingGeometry, saveFloatingGeometry } from '@/store/floating-overlay'

import { useRouteEnumParam } from '../hooks/use-route-enum-param'

import { DetachableOverlay } from './detachable-overlay'
import { OverlayView } from './overlay-view'

const TABS = ['a', 'b'] as const

function Overlay({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useRouteEnumParam('tab', TABS, 'a')

  return (
    <OverlayView onClose={onClose}>
      <span data-testid="tab">{tab}</span>
      <button onClick={() => setTab('b')} type="button">
        tab b
      </button>
    </OverlayView>
  )
}

function Harness() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeOpen = location.pathname === '/settings'

  return (
    <>
      <span data-testid="path">{location.pathname}</span>
      <Routes>
        <Route element={null} path="*" />
      </Routes>
      <DetachableOverlay
        closeToPreviousRoute={() => navigate('/session/1', { replace: true })}
        route="/settings"
        routeOpen={routeOpen}
        view="settings"
      >
        {onClose => <Overlay onClose={onClose} />}
      </DetachableOverlay>
    </>
  )
}

describe('DetachableOverlay (AIS-398)', () => {
  afterEach(() => {
    cleanup()
    $floatingOverlay.set(null)
    window.localStorage.clear()
  })

  it('floats over the chat route, keeps its tab state and docks back via the route', () => {
    const rendered = render(
      <MemoryRouter initialEntries={['/settings']}>
        <Harness />
      </MemoryRouter>
    )

    fireEvent.click(rendered.getByText('tab b'))
    expect(rendered.getByTestId('tab').textContent).toBe('b')

    fireEvent.click(rendered.getByLabelText(/Detach as a window|Als Fenster lösen/))

    expect(rendered.getByTestId('path').textContent).toBe('/session/1')
    expect($floatingOverlay.get()).toBe('settings')
    expect(rendered.container.querySelector('[data-floating]')).toBeTruthy()
    expect(rendered.getByTestId('tab').textContent).toBe('b')

    fireEvent.click(rendered.getByLabelText(/Dock back|andocken/))

    expect(rendered.getByTestId('path').textContent).toBe('/settings')
    expect($floatingOverlay.get()).toBeNull()
    expect(rendered.container.querySelector('[data-floating]')).toBeNull()
  })

  it('renders a floating window without a backdrop and closes it by clearing the floating view', () => {
    $floatingOverlay.set('settings')

    const rendered = render(
      <MemoryRouter initialEntries={['/session/1']}>
        <Harness />
      </MemoryRouter>
    )

    const root = rendered.container.querySelector('[data-floating]')
    expect(root).toBeTruthy()
    expect(root?.className).toContain('pointer-events-none')

    // Clicking outside the window does nothing.
    fireEvent.click(root as Element)
    expect($floatingOverlay.get()).toBe('settings')

    fireEvent.click(rendered.getByLabelText(/Close|Schließen/))
    expect($floatingOverlay.get()).toBeNull()
    expect(rendered.container.querySelector('[data-floating]')).toBeNull()
  })

  it('stays hidden when neither routed nor floating', () => {
    const rendered = render(
      <MemoryRouter initialEntries={['/session/1']}>
        <Harness />
      </MemoryRouter>
    )

    expect(rendered.queryByTestId('tab')).toBeNull()
  })
})

describe('floating geometry', () => {
  afterEach(() => window.localStorage.clear())

  it('round-trips saved geometry and fits it into the window', () => {
    saveFloatingGeometry('cron', { height: 500, width: 600, x: 10, y: 20 })
    expect(loadFloatingGeometry('cron')).toEqual({ height: 500, width: 600, x: 10, y: 20 })

    saveFloatingGeometry('cron', { height: 99_999, width: 99_999, x: 99_999, y: -50 })
    const fitted = loadFloatingGeometry('cron')
    expect(fitted.width).toBe(window.innerWidth)
    expect(fitted.height).toBe(window.innerHeight)
    expect(fitted.x).toBe(0)
    expect(fitted.y).toBe(0)
  })
})
