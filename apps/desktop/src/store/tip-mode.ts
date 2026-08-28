import { atom } from 'nanostores'

export type TipMode = 'auto' | 'business' | 'nerd'

const STORAGE_KEY = 'hermes.desktop.tipMode'

function loadStoredTipMode(): TipMode {
  if (typeof window === 'undefined') {
    return 'auto'
  }

  try {
    const val = localStorage.getItem(STORAGE_KEY)

    if (val === 'business' || val === 'nerd' || val === 'auto') {
      return val
    }
  } catch {
    // Ignore
  }

  return 'auto'
}

export const $tipMode = atom<TipMode>(loadStoredTipMode())

$tipMode.subscribe(mode => {
  if (typeof window === 'undefined') {return}

  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // Ignore
  }
})

export function setTipMode(mode: TipMode) {
  $tipMode.set(mode)
}

export function resolveIsNerdyMode(tipMode: TipMode, defaultIsIamds: boolean): boolean {
  if (tipMode === 'nerd') {return true}

  if (tipMode === 'business') {return false}

  return defaultIsIamds
}
