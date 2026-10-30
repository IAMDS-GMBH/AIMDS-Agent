import { defineConfig } from '@playwright/test'

// Screenshot check (AIS-798): the built renderer (`vite build`) served by
// `vite preview`, driven by headless Chromium with a fake desktop bridge and
// fixtures. Baselines are per OS — font rasterisers differ (DirectWrite,
// Core Text, FreeType) even with the bundled faces.
const PORT = 4179

export default defineConfig({
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      // Up to 0.1 % of pixels may differ by more than `threshold` (anti-aliasing
      // noise between runner images). Swapping the UI font changes ~0.5 % of
      // the near-empty new-chat view and 2 % of the chat; a layout shift more.
      maxDiffPixelRatio: 0.001,
      scale: 'css',
      stylePath: './screenshot.css',
      threshold: 0.2
    }
  },
  forbidOnly: Boolean(process.env.CI),
  outputDir: '../test-results/screenshots',
  reporter: process.env.CI ? [['list'], ['html', { open: 'never', outputFolder: '../playwright-report' }]] : 'list',
  retries: 0,
  snapshotPathTemplate: '{testDir}/baselines/{platform}/{arg}{ext}',
  testDir: '.',
  testMatch: 'views.spec.ts',
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    colorScheme: 'light',
    deviceScaleFactor: 1,
    locale: 'en-US',
    reducedMotion: 'reduce',
    timezoneId: 'Europe/Berlin',
    viewport: { height: 800, width: 1220 }
  },
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    cwd: '..',
    // CI re-recording after a failed comparison: see desktop-screenshots.yml.
    reuseExistingServer: !process.env.CI || process.env.SCREENSHOTS_REUSE_SERVER === '1',
    url: `http://127.0.0.1:${PORT}`
  },
  workers: process.env.CI ? 1 : undefined
})
