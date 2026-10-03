import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'

import { $floatingOverlay, type FloatableOverlay, setFloatingOverlay } from '@/store/floating-overlay'

import { OverlayFloatingContext } from './overlay-view'

interface DetachableOverlayProps {
  /** Rendered with the close handler that fits the current mode. */
  children: (onClose: () => void) => ReactNode
  closeToPreviousRoute: () => void
  route: string
  routeOpen: boolean
  view: FloatableOverlay
}

/** A route overlay that can be detached into a floating window (AIS-398).
 *
 *  Detaching marks the view floating, then returns to the previous route, so
 *  the chat behind it is live again. Docking navigates back to the overlay
 *  route. The children stay mounted across both switches, so the overlay
 *  keeps its state. */
export function DetachableOverlay({ children, closeToPreviousRoute, route, routeOpen, view }: DetachableOverlayProps) {
  const floatingOverlay = useStore($floatingOverlay)
  const navigate = useNavigate()
  const floating = !routeOpen && floatingOverlay === view

  // Opening the route (dock button, menu, shortcut) replaces the window.
  useEffect(() => {
    if (routeOpen && $floatingOverlay.get() === view) {
      setFloatingOverlay(null)
    }
  }, [routeOpen, view])

  const value = useMemo(
    () => ({
      floating,
      onToggleFloating: () => {
        if (floating) {
          navigate(route)
        } else {
          setFloatingOverlay(view)
          closeToPreviousRoute()
        }
      },
      view
    }),
    [closeToPreviousRoute, floating, navigate, route, view]
  )

  if (!routeOpen && !floating) {
    return null
  }

  return (
    <OverlayFloatingContext.Provider value={value}>
      {children(floating ? () => setFloatingOverlay(null) : closeToPreviousRoute)}
    </OverlayFloatingContext.Provider>
  )
}
