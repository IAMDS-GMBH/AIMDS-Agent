import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

describe('common.open (AIS-295)', () => {
  it('resolves in both shipped locales', () => {
    setRuntimeI18nLocale('de')
    expect(translateNow('common.open')).toBe('Öffnen')
    setRuntimeI18nLocale('en')
    expect(translateNow('common.open')).toBe('Open')
  })

  it('warns once per missing key in dev builds', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    setRuntimeI18nLocale('en')
    translateNow('missing.once')
    translateNow('missing.once')
    expect(warn.mock.calls.filter(([msg]) => String(msg).includes('missing.once'))).toHaveLength(1)
    warn.mockRestore()
  })
})
