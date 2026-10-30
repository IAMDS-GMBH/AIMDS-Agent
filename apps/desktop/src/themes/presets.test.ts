import { describe, expect, it } from 'vitest'

import { textTokensFor } from './context'
import {
  BUILTIN_THEME_LIST,
  BUNDLED_MONO_FAMILY,
  BUNDLED_SANS_FAMILY,
  DEFAULT_SKIN_NAME,
  DEFAULT_TYPOGRAPHY,
  EMOJI_FALLBACK,
  nousTheme
} from './presets'

// AIS-794: the default skin is the AIMDS Suite look — its exact surfaces,
// text, hairlines and accent, painted without the neutral grey mix.
describe('AIMDS Suite skin (AIS-794)', () => {
  it('is the default skin and paints its palette as given', () => {
    expect(DEFAULT_SKIN_NAME).toBe(nousTheme.name)
    expect(nousTheme.exactPalette).toBe(true)
  })

  it('uses the Suite tokens in dark mode', () => {
    expect(nousTheme.darkColors).toMatchObject({
      background: '#0B1020',
      border: '#232C54',
      card: '#161D3D',
      foreground: '#E7ECFF',
      glow: '#1B2761',
      mutedForeground: '#98A2C8',
      primary: '#4D7CFF',
      ring: '#6C95FF'
    })
  })

  it('ships a light variant with a readable accent on white', () => {
    const light = nousTheme.colors

    expect(light.background).not.toBe(nousTheme.darkColors?.background)
    expect(light.card).toBe('#FFFFFF')
    // White button text on the light accent needs at least 4.5:1.
    expect(contrast(light.primary, light.primaryForeground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(light.foreground, light.background)).toBeGreaterThanOrEqual(7)
  })

  it('maps muted copy to the palette only for an exact palette', () => {
    const dark = nousTheme.darkColors!

    expect(textTokensFor(dark, true)['--ui-text-tertiary']).toBe('#98A2C8')
    // Other skins clear the override so the styles.css fade applies again.
    expect(textTokensFor(dark, false)).toEqual({ '--ui-text-secondary': '', '--ui-text-tertiary': '' })
  })
})

function luminance(hex: string): number {
  const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
  const [r, g, b] = channels.map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))

  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)

  return (hi + 0.05) / (lo + 0.05)
}

// #40364: none of the UI text/mono fonts carry emoji glyphs, so every font
// stack must end with a color-emoji fallback or emoji render as tofu on
// platforms whose default font lacks them (e.g. Linux).
describe('theme typography emoji fallback (#40364)', () => {
  const stacks: Array<[string, string]> = [
    ['DEFAULT_TYPOGRAPHY.fontSans', DEFAULT_TYPOGRAPHY.fontSans],
    ['DEFAULT_TYPOGRAPHY.fontMono', DEFAULT_TYPOGRAPHY.fontMono],
    // A theme may override only fontMono (fontSans then falls back to the
    // default, which already carries the emoji stack), so skip undefined.
    ...BUILTIN_THEME_LIST.flatMap(theme =>
      (
        [
          [`${theme.name}.fontSans`, theme.typography?.fontSans],
          [`${theme.name}.fontMono`, theme.typography?.fontMono]
        ] as Array<[string, string | undefined]>
      ).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    )
  ]

  it.each(stacks)('%s includes a color-emoji font', (_label, stack) => {
    expect(stack).toMatch(/Apple Color Emoji|Segoe UI Emoji|Noto Color Emoji|(^|,\s*)emoji\b/)
  })

  it('EMOJI_FALLBACK lists the major platform emoji fonts', () => {
    expect(EMOJI_FALLBACK).toContain('Apple Color Emoji')
    expect(EMOJI_FALLBACK).toContain('Segoe UI Emoji')
    expect(EMOJI_FALLBACK).toContain('Noto Color Emoji')
  })
})

// AIS-798: text must look the same on Windows, macOS and Linux, so every
// built-in stack leads with a bundled face and no built-in theme pulls a font
// from the network.
describe('theme typography uses the bundled faces (AIS-798)', () => {
  const firstFamily = (stack: string) => stack.split(',')[0]?.trim()
  const bundled = [BUNDLED_SANS_FAMILY, BUNDLED_MONO_FAMILY]

  it('defaults to the bundled sans and mono', () => {
    expect(firstFamily(DEFAULT_TYPOGRAPHY.fontSans)).toBe(BUNDLED_SANS_FAMILY)
    expect(firstFamily(DEFAULT_TYPOGRAPHY.fontMono)).toBe(BUNDLED_MONO_FAMILY)
  })

  it.each(BUILTIN_THEME_LIST.map(theme => [theme.name, theme] as const))(
    '%s leads with a bundled face',
    (_name, theme) => {
      for (const stack of [theme.typography?.fontSans, theme.typography?.fontMono]) {
        if (stack) {
          expect(bundled).toContain(firstFamily(stack))
        }
      }

      expect(theme.typography?.fontUrl).toBeUndefined()
    }
  )
})
