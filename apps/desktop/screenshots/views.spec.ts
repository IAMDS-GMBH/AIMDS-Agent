import { expect, type Page, test } from '@playwright/test'

import { installFakeDesktop } from './fake-desktop'
import { FIXTURES, FROZEN_NOW } from './fixtures'
import { type ScreenshotView, VIEWS } from './views'

// Bundled faces only (AIS-798): the screenshot must never depend on which
// system fonts the runner happens to have.
const BUNDLED_FACES = ['13px "Inter Variable"', '600 13px "Inter Variable"', '13px "JetBrains Mono Variable"']
const DEBUG = Boolean(process.env.SCREENSHOTS_DEBUG)
const READY_TIMEOUT_MS = 20_000
// The app has settled once the fake desktop saw no new call for this long.
const QUIET_MS = 750

const fakeDesktopCalls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __fakeDesktopCalls?: string[] }).__fakeDesktopCalls ?? [])

async function waitForQuietDesktop(page: Page) {
  const deadline = Date.now() + READY_TIMEOUT_MS
  let seen = -1
  let quietSince = Date.now()

  while (Date.now() < deadline) {
    const count = (await fakeDesktopCalls(page)).length

    if (count !== seen) {
      seen = count
      quietSince = Date.now()
    } else if (Date.now() - quietSince >= QUIET_MS) {
      return
    }

    await page.waitForTimeout(100)
  }
}

async function settle(page: Page, view: ScreenshotView) {
  await page.evaluate(async faces => {
    await Promise.all(faces.map(face => document.fonts.load(face)))
    await document.fonts.ready
  }, BUNDLED_FACES)
  await waitForQuietDesktop(page)

  if (view.scrollToTop) {
    await page
      .locator(view.scrollToTop)
      .first()
      .evaluate(element => element.scrollTo({ top: 0 }))
  }

  // Two frames for layout that reacts to the scroll / late measurements.
  await page.evaluate(
    () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))))
  )
}

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(FROZEN_NOW)
  await page.addInitScript(installFakeDesktop, FIXTURES)
})

for (const view of VIEWS) {
  test(view.name, async ({ page }) => {
    // An uncaught error means the view (or its fixtures) is broken; never bake
    // that into a baseline.
    const pageErrors: string[] = []
    const consoleProblems: string[] = []

    page.on('pageerror', error => pageErrors.push(error.stack ?? error.message))

    if (DEBUG) {
      page.on('console', message => {
        if (message.type() === 'error' || message.type() === 'warning') {
          consoleProblems.push(`${message.type()}: ${message.text()}`)
        }
      })
    }

    await page.goto(`/#${view.route}`)

    try {
      await page.locator(view.ready).first().waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS })
      await settle(page, view)
    } finally {
      if (DEBUG) {
        await page.screenshot({ path: test.info().outputPath('debug.png') })
        console.log(`[${view.name}] fake desktop calls:\n  ${[...new Set(await fakeDesktopCalls(page))].join('\n  ')}`)
        console.log(`[${view.name}] console:\n  ${consoleProblems.join('\n  ') || '(clean)'}`)
      }
    }

    expect(pageErrors, 'uncaught errors in the page').toEqual([])
    await expect(page).toHaveScreenshot(`${view.name}.png`)
  })
}
