/**
 * The views the screenshot check captures (AIS-798). Adding a view is one
 * line here: a name (the baseline file name), the hash route and a selector
 * that is visible once the view has rendered its content. Data the view needs
 * goes into fixtures.ts. Then record its baselines (see apps/desktop/README.md).
 */

import { LONG_ANSWER_SESSION_ID } from './fixtures'

export interface ScreenshotView {
  /** Baseline file: baselines/<platform>/<name>.png */
  name: string
  /** Hash route, as in `#/settings`. */
  route: string
  /** Visible once the view has rendered its content. */
  ready: string
  /** Scroll this element to the top before the capture (e.g. a long thread). */
  scrollToTop?: string
}

const THREAD = '[data-slot="aui_thread-viewport"]'

export const VIEWS: readonly ScreenshotView[] = [
  { name: 'new-chat', route: '/', ready: '[data-slot="composer-rich-input"]' },
  { name: 'chat-long-answer', route: `/${LONG_ANSWER_SESSION_ID}`, ready: 'table', scrollToTop: THREAD },
  { name: 'settings', route: '/settings', ready: '[data-slot="select-trigger"]' }
]
