import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import { setRuntimeI18nLocale, translateNow } from './runtime'

describe('desktop i18n runtime translator', () => {
  beforeEach(() => {
    setRuntimeI18nLocale('de')
  })

  afterEach(() => {
    setRuntimeI18nLocale('de')
  })

  it('translates string paths for the active runtime locale', () => {
    setRuntimeI18nLocale('de')

    expect(translateNow('boot.ready')).toBe('Hermes Desktop ist bereit')
    expect(translateNow('language.label')).toBe('Sprache')
  })

  it('passes arguments to function translations', () => {
    setRuntimeI18nLocale('en')
    expect(translateNow('notifications.updateReadyMessage', 2)).toBe('2 new changes available.')
  })

  it('falls back to default locale when the active locale cannot resolve a key', () => {
    const boot = TRANSLATIONS.en.boot as { ready?: string }
    const originalReady = boot.ready

    try {
      boot.ready = undefined
      setRuntimeI18nLocale('en')

      expect(translateNow('boot.ready')).toBe('Hermes Desktop ist bereit')
    } finally {
      boot.ready = originalReady
    }
  })

  it('returns the key when no locale can resolve a path', () => {
    setRuntimeI18nLocale('en')

    expect(translateNow('missing.path')).toBe('missing.path')
  })
})
