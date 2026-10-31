/**
 * The views the screenshot check captures (AIS-798). Adding a view is one
 * line here: a name (the baseline file name), the hash route and a selector
 * that is visible once the view has rendered its content. Data the view needs
 * goes into fixtures.ts. Then record its baselines (see apps/desktop/README.md).
 */

import { LONG_ANSWER_SESSION_ID, PROFILES_VIEW_API } from './fixtures'

export interface ScreenshotView {
  /** Baseline file: baselines/<platform>/<name>.png */
  name: string
  /** Hash route, as in `#/settings`. */
  route: string
  /** Visible once the view has rendered its content. */
  ready: string
  /** Scroll this element to the top before the capture (e.g. a long thread). */
  scrollToTop?: string
  /** OS colour scheme for this capture (default light, see the config). */
  colorScheme?: 'dark' | 'light'
  /** Extra REST answers for this view only, on top of fixtures.ts. */
  api?: Record<string, unknown>
}

const THREAD = '[data-slot="aui_thread-viewport"]'

export const VIEWS: readonly ScreenshotView[] = [
  { name: 'new-chat', route: '/', ready: '[data-slot="composer-rich-input"]' },
  { name: 'chat-long-answer', route: `/${LONG_ANSWER_SESSION_ID}`, ready: 'table', scrollToTop: THREAD },
  { name: 'settings', route: '/settings', ready: '[data-slot="select-trigger"]' },
  // AIS-794: the Suite look in dark, with every sidebar section filled
  // (Im Hintergrund, Favoriten, sessions by day, account menu).
  { colorScheme: 'dark', name: 'sidebar-sections-dark', route: '/', ready: '[data-testid="sidebar-background"]' },
  // AIS-794: Darstellung with the Entwickleransicht switch, and Fähigkeiten.
  { name: 'settings-appearance', route: '/settings?tab=config:appearance', ready: '[role="switch"]' },
  { name: 'settings-skills', route: '/settings?tab=skills', ready: '[role="switch"]' },
  // AIS-794: colour and order per profile, on the page "Profile verwalten" opens.
  { api: PROFILES_VIEW_API, name: 'profiles', route: '/profiles', ready: '[data-testid="profile-row-work"]' }
]
