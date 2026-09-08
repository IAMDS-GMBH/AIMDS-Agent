import { atom } from 'nanostores'

import type { PreviewAutoOpenMode } from '@/lib/preview-auto-open'

const STORAGE_KEY = 'hermes.desktop.previewAutoOpen'
const DEFAULT_MODE: PreviewAutoOpenMode = 'artifacts'

function isMode(value: unknown): value is PreviewAutoOpenMode {
  return value === 'artifacts' || value === 'all' || value === 'never'
}

function loadStoredMode(): PreviewAutoOpenMode {
  if (typeof window === 'undefined') {
    return DEFAULT_MODE
  }

  try {
    const value = localStorage.getItem(STORAGE_KEY)

    if (isMode(value)) {
      return value
    }
  } catch {
    // Ignore — storage may be unavailable.
  }

  return DEFAULT_MODE
}

// Which tool results may pop the preview pane on their own (AIS-305 B).
// Persisted per machine like the tip mode; the routing hook reads it with
// `.get()` at event time so a change applies to the next tool result.
export const $previewAutoOpen = atom<PreviewAutoOpenMode>(loadStoredMode())

$previewAutoOpen.subscribe(mode => {
  if (typeof window === 'undefined') {
    return
  }

  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // Ignore
  }
})

export function setPreviewAutoOpen(mode: PreviewAutoOpenMode) {
  $previewAutoOpen.set(mode)
}
