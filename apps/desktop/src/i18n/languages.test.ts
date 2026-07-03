import { describe, expect, it } from 'vitest'

import { DEFAULT_LOCALE, isLocale, isSupportedLocaleValue, localeConfigValue, normalizeLocale } from './languages'

describe('desktop i18n languages', () => {
  it('normalizes supported locale aliases', () => {
    expect(normalizeLocale('de')).toBe('de')
    expect(normalizeLocale('DE-CH')).toBe('de')
    expect(normalizeLocale('en')).toBe('en')
    expect(normalizeLocale('EN-US')).toBe('en')
  })

  it('falls back to default locale for empty or unsupported values', () => {
    expect(normalizeLocale(null)).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('zh')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('ja-JP')).toBe(DEFAULT_LOCALE)
  })

  it('distinguishes exact locale ids from supported config aliases', () => {
    expect(isSupportedLocaleValue('de-DE')).toBe(true)
    expect(isSupportedLocaleValue('en-US')).toBe(true)
    expect(isSupportedLocaleValue('zh-CN')).toBe(false)
    expect(isLocale('de-DE')).toBe(false)
    expect(isLocale('de')).toBe(true)
    expect(isLocale('en')).toBe(true)
  })

  it('returns the persisted config value for supported locales', () => {
    expect(localeConfigValue('de')).toBe('de')
    expect(localeConfigValue('en')).toBe('en')
  })
})
