import { describe, expect, it } from 'vitest'

import {
  BUILTIN_THEME_LIST,
  BUNDLED_MONO_FAMILY,
  BUNDLED_SANS_FAMILY,
  DEFAULT_TYPOGRAPHY,
  EMOJI_FALLBACK
} from './presets'

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
