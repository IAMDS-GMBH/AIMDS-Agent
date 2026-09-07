import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE } from './languages'
import type { Locale, Translations } from './types'

let runtimeLocale: Locale = DEFAULT_LOCALE

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function resolvePath(catalog: Translations, key: string): unknown {
  return key.split('.').reduce<unknown>((current, part) => {
    if (!isRecord(current)) {
      return undefined
    }

    return current[part]
  }, catalog)
}

function renderTranslation(value: unknown, args: unknown[]): string | null {
  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'function') {
    return (value as (...args: unknown[]) => string)(...args)
  }

  return null
}

export function setRuntimeI18nLocale(locale: Locale) {
  runtimeLocale = locale
}

export function translateNow(key: string, ...args: unknown[]): string {
  const active = renderTranslation(resolvePath(TRANSLATIONS[runtimeLocale], key), args)

  if (active !== null) {
    return active
  }

  if (runtimeLocale !== DEFAULT_LOCALE) {
    const fallback = renderTranslation(resolvePath(TRANSLATIONS[DEFAULT_LOCALE], key), args)

    if (fallback !== null) {
      return fallback
    }
  }

  warnMissingKey(key)

  return key
}

const warnedMissingKeys = new Set<string>()

// A miss returns the raw key so the UI never renders empty — which also
// means a `|| 'fallback'` at the call site never fires and a typo ships
// silently (AIS-295: the cron toast showed "common.open"). Surface each
// missing key once in dev builds so it is caught before release.
function warnMissingKey(key: string) {
  if (warnedMissingKeys.has(key)) {
    return
  }

  warnedMissingKeys.add(key)

  if (import.meta.env.DEV) {
    console.warn(`[i18n] missing translation key: ${key}`)
  }
}
