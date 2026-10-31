import { atom, computed, type ReadableAtom } from 'nanostores'

import { persistString, persistStringRecord, storedString, storedStringRecord } from '@/lib/storage'

import { $activeGatewayProfile, normalizeProfileKey } from './profile'

// AIS-794: the desktop is the co-worker posture (AIS-309 — the terminal CLI is
// the developer, the desktop the co-worker), so normal users get the calm
// shell. "Entwickleransicht" (developer view, off by default) unlocks the
// developer surfaces: Entdecken (/hub), the developer-only navigation and the
// "Technische Details" switch for the status bar (gateway, agents, cron, token
// numbers, tool count, session timer).
//
// Both are stored per profile like the skin and light/dark mode: "default"
// reads/writes the global slot, a named profile its own entry and falls back
// to the global value until it has one.
const DEVELOPER_VIEW_KEY = 'hermes-desktop-developer-view-v1'
const PROFILE_DEVELOPER_VIEW_KEY = 'hermes-desktop-profile-developer-view-v1'
const TECHNICAL_DETAILS_KEY = 'hermes-desktop-technical-details-v1'
const PROFILE_TECHNICAL_DETAILS_KEY = 'hermes-desktop-profile-technical-details-v1'

// Bumped on every write so the computed atoms re-read storage.
const $prefsRevision = atom(0)

export interface ProfileFlag {
  resolve: (profile: string) => boolean
  assign: (profile: string, value: boolean) => void
}

export function profileFlag(record: string, global: string, fallback: boolean): ProfileFlag {
  return {
    resolve: profile => {
      const raw = storedStringRecord(record)[profile] ?? storedString(global)

      return raw === null ? fallback : raw === 'true'
    },
    assign: (profile, value) => {
      if (profile === 'default') {
        persistString(global, String(value))
      } else {
        persistStringRecord(record, { ...storedStringRecord(record), [profile]: String(value) })
      }

      $prefsRevision.set($prefsRevision.get() + 1)
    }
  }
}

export const developerViewPref = profileFlag(PROFILE_DEVELOPER_VIEW_KEY, DEVELOPER_VIEW_KEY, false)
// On by default once the developer view is on: a developer who opts in gets
// the full technical status bar and can still switch it off.
export const technicalDetailsPref = profileFlag(PROFILE_TECHNICAL_DETAILS_KEY, TECHNICAL_DETAILS_KEY, true)

const activeProfileKey = () => normalizeProfileKey($activeGatewayProfile.get())

/** The user's own switch for the active profile (shown in Settings). */
export const $developerView: ReadableAtom<boolean> = computed([$activeGatewayProfile, $prefsRevision], profile =>
  developerViewPref.resolve(normalizeProfileKey(profile))
)

/** The "Technische Details" switch as stored (shown only in developer view). */
export const $technicalDetailsSetting: ReadableAtom<boolean> = computed(
  [$activeGatewayProfile, $prefsRevision],
  profile => technicalDetailsPref.resolve(normalizeProfileKey(profile))
)

/** Effective: technical status bar items show only in developer view. */
export const $technicalDetails: ReadableAtom<boolean> = computed(
  [$developerView, $technicalDetailsSetting],
  (developer, details) => developer && details
)

export function setDeveloperView(on: boolean) {
  developerViewPref.assign(activeProfileKey(), on)
}

export function setTechnicalDetails(on: boolean) {
  technicalDetailsPref.assign(activeProfileKey(), on)
}
