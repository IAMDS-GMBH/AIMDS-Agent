import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { de } from '@/i18n/de'
import { en } from '@/i18n/en'

import { PRODUCT_DISPLAY_NAME } from './product'

// AIS-755: the visible product name has one value; the main process and the
// bootstrap installer carry copies (no shared module between the bundles).
const desktopRoot = resolve(__dirname, '../..')

describe('PRODUCT_DISPLAY_NAME', () => {
  it('is the same literal in the main process and the installer', () => {
    const literal = `const PRODUCT_DISPLAY_NAME = '${PRODUCT_DISPLAY_NAME}'`

    expect(readFileSync(resolve(desktopRoot, 'electron/main.cjs'), 'utf8')).toContain(literal)
    expect(readFileSync(resolve(desktopRoot, '../bootstrap-installer/src/lib/product.ts'), 'utf8')).toContain(
      `export ${literal}`
    )
  })

  it('names the product on the install and update screens, in both languages', () => {
    for (const t of [de, en]) {
      const texts = [
        t.updates.stages.restart,
        t.updates.unsupportedMessage,
        t.updates.manualBody,
        t.updates.manualPickedUp,
        t.updates.applyingBody,
        t.updates.applyingClose,
        t.install.oneTimeTitle,
        t.install.settingUpTitle,
        t.install.activeDesc
      ]

      for (const text of texts) {
        expect(text).toContain(PRODUCT_DISPLAY_NAME)
        expect(text).not.toMatch(/Hermes/)
      }
    }
  })
})
