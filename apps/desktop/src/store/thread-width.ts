import { atom } from 'nanostores'

import { persistString, persistStringRecord, storedString, storedStringRecord } from '@/lib/storage'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'

/**
 * "Chat width" (AIS-792): how wide the message column and the composer may
 * grow. Normal is the old 48.75rem column, Wide (the default) caps at 64rem,
 * Full uses the whole pane minus the gutter. styles.css maps the value
 * (data-thread-width on <html>) onto --thread-width.
 *
 * Stored per profile like the theme: the "default" profile is the global slot,
 * named profiles get their own entry and inherit the global until assigned.
 */
export type ThreadWidth = 'full' | 'normal' | 'wide'

export const THREAD_WIDTHS: readonly ThreadWidth[] = ['normal', 'wide', 'full']
export const DEFAULT_THREAD_WIDTH: ThreadWidth = 'wide'

const GLOBAL_KEY = 'hermes-desktop-thread-width-v1'
const PROFILE_KEY = 'hermes-desktop-profile-thread-widths-v1'
// Written by the theme context; lets the first paint use the last profile.
const LAST_PROFILE_KEY = 'hermes-desktop-active-profile-v1'

export function normalizeThreadWidth(value: null | string | undefined): ThreadWidth {
  return value === 'normal' || value === 'wide' || value === 'full' ? value : DEFAULT_THREAD_WIDTH
}

export function resolveThreadWidth(profile: string): ThreadWidth {
  return normalizeThreadWidth(storedStringRecord(PROFILE_KEY)[profile] ?? storedString(GLOBAL_KEY))
}

export function assignThreadWidth(profile: string, value: ThreadWidth): void {
  if (profile === 'default') {
    persistString(GLOBAL_KEY, value)
  } else {
    persistStringRecord(PROFILE_KEY, { ...storedStringRecord(PROFILE_KEY), [profile]: value })
  }
}

export const $threadWidth = atom<ThreadWidth>(
  typeof window === 'undefined'
    ? DEFAULT_THREAD_WIDTH
    : resolveThreadWidth(normalizeProfileKey(storedString(LAST_PROFILE_KEY)))
)

export function applyThreadWidth(value: ThreadWidth, root: HTMLElement = document.documentElement): void {
  if (value === DEFAULT_THREAD_WIDTH) {
    delete root.dataset.threadWidth
  } else {
    root.dataset.threadWidth = value
  }
}

/** The user picked a width: show it and keep it for the active profile. */
export function setThreadWidth(value: ThreadWidth): void {
  $threadWidth.set(value)
  assignThreadWidth(normalizeProfileKey($activeGatewayProfile.get()), value)
}

let installed = false

/** Paint the stored width now and follow the setting and profile switches. */
export function installThreadWidth(): void {
  if (installed || typeof document === 'undefined') {
    return
  }

  installed = true
  $threadWidth.subscribe(value => applyThreadWidth(value))
  // `listen`, not `subscribe`: until the gateway reports its profile the atom
  // still holds the placeholder "default", and the boot value must stay.
  $activeGatewayProfile.listen(profile => $threadWidth.set(resolveThreadWidth(normalizeProfileKey(profile))))
}
