import { useCallback, useSyncExternalStore } from 'react'

import { nativeControlsWidth } from './titlebar'

// Chromium's Window Controls Overlay API; Electron exposes it whenever a
// window uses `titleBarOverlay`. Not in TypeScript's DOM lib yet.
interface WindowControlsOverlay extends EventTarget {
  getTitlebarAreaRect: () => DOMRect
  visible: boolean
}

function windowControlsOverlay(): WindowControlsOverlay | null {
  if (typeof navigator === 'undefined') {
    return null
  }

  return (navigator as Navigator & { windowControlsOverlay?: WindowControlsOverlay }).windowControlsOverlay ?? null
}

/** The measured width of the right-edge OS window controls (see nativeControlsWidth). */
export function useNativeControlsWidth(reported: number): number {
  const subscribe = useCallback((onChange: () => void) => {
    const overlay = windowControlsOverlay()

    overlay?.addEventListener('geometrychange', onChange)
    window.addEventListener('resize', onChange)

    return () => {
      overlay?.removeEventListener('geometrychange', onChange)
      window.removeEventListener('resize', onChange)
    }
  }, [])

  const snapshot = useCallback(() => {
    const overlay = windowControlsOverlay()
    const rect = overlay?.visible ? overlay.getTitlebarAreaRect() : null

    return nativeControlsWidth(reported, rect, window.innerWidth)
  }, [reported])

  return useSyncExternalStore(subscribe, snapshot, () => reported)
}
