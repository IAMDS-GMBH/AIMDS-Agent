import { atom } from 'nanostores'

/** Full-screen overlays that can be detached into a floating window (AIS-398). */
export type FloatableOverlay = 'agents' | 'command-center' | 'cron' | 'profiles' | 'settings'

export interface FloatingGeometry {
  height: number
  width: number
  x: number
  y: number
}

const GEOMETRY_STORAGE_KEY = 'hermes.desktop.floatingOverlayGeometry.v1'

export const FLOATING_MIN_WIDTH = 480
export const FLOATING_MIN_HEIGHT = 360

/** The overlay currently shown as a floating window, if any. One at a time. */
export const $floatingOverlay = atom<FloatableOverlay | null>(null)

export function setFloatingOverlay(view: FloatableOverlay | null) {
  $floatingOverlay.set(view)
}

function readAllGeometry(): Partial<Record<FloatableOverlay, FloatingGeometry>> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(GEOMETRY_STORAGE_KEY) || '{}')

    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function defaultFloatingGeometry(): FloatingGeometry {
  const width = Math.max(FLOATING_MIN_WIDTH, Math.min(1000, Math.round(window.innerWidth * 0.6)))
  const height = Math.max(FLOATING_MIN_HEIGHT, Math.min(760, Math.round(window.innerHeight * 0.8)))

  // Right-hand side, so the chat column on the left stays visible.
  return { height, width, x: Math.max(0, window.innerWidth - width - 24), y: 48 }
}

/** Saved geometry for `view`, fitted into the current window size. */
export function loadFloatingGeometry(view: FloatableOverlay): FloatingGeometry {
  const saved = readAllGeometry()[view]
  const base = saved && [saved.x, saved.y, saved.width, saved.height].every(Number.isFinite) ? saved : defaultFloatingGeometry()
  const width = Math.min(Math.max(base.width, FLOATING_MIN_WIDTH), window.innerWidth)
  const height = Math.min(Math.max(base.height, FLOATING_MIN_HEIGHT), window.innerHeight)

  return {
    height,
    width,
    x: Math.min(Math.max(base.x, 0), Math.max(0, window.innerWidth - width)),
    y: Math.min(Math.max(base.y, 0), Math.max(0, window.innerHeight - height))
  }
}

export function saveFloatingGeometry(view: FloatableOverlay, geometry: FloatingGeometry) {
  try {
    window.localStorage.setItem(GEOMETRY_STORAGE_KEY, JSON.stringify({ ...readAllGeometry(), [view]: geometry }))
  } catch {
    // Best effort: the window just opens at the default spot next time.
  }
}
