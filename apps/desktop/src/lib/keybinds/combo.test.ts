import { afterEach, describe, expect, it, vi } from 'vitest'

// `IS_MAC` is resolved once at module load from `navigator`, so each platform
// case overrides the platform and re-imports the module fresh.
async function loadCombo(platform: string) {
  Object.defineProperty(window.navigator, 'platform', { value: platform, configurable: true })
  vi.resetModules()

  return import('./combo')
}

function keydown(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', init)
}

afterEach(() => {
  vi.resetModules()
})

describe('comboFromEvent — ctrl as a distinct modifier on macOS', () => {
  it('reports Control+Tab as "ctrl+tab" on macOS (not Cmd)', async () => {
    const { comboFromEvent } = await loadCombo('MacIntel')

    expect(comboFromEvent(keydown({ code: 'Tab', ctrlKey: true }))).toBe('ctrl+tab')
    expect(comboFromEvent(keydown({ code: 'Tab', ctrlKey: true, shiftKey: true }))).toBe('ctrl+shift+tab')
  })

  it('keeps Cmd as "mod" and distinct from Control on macOS', async () => {
    const { comboFromEvent } = await loadCombo('MacIntel')

    expect(comboFromEvent(keydown({ code: 'KeyK', metaKey: true }))).toBe('mod+k')
    expect(comboFromEvent(keydown({ code: 'KeyK', ctrlKey: true }))).toBe('ctrl+k')
  })

  it('treats Control as the "mod" accelerator off macOS', async () => {
    const { comboFromEvent } = await loadCombo('Win32')

    expect(comboFromEvent(keydown({ code: 'Tab', ctrlKey: true }))).toBe('mod+tab')
    expect(comboFromEvent(keydown({ code: 'Tab', ctrlKey: true, shiftKey: true }))).toBe('mod+shift+tab')
  })
})

describe('canonicalizeCombo', () => {
  it('leaves "ctrl+…" untouched on macOS', async () => {
    const { canonicalizeCombo } = await loadCombo('MacIntel')

    expect(canonicalizeCombo('ctrl+tab')).toBe('ctrl+tab')
    expect(canonicalizeCombo('ctrl+shift+tab')).toBe('ctrl+shift+tab')
  })

  it('folds "ctrl+…" to "mod+…" off macOS so a real Control press resolves', async () => {
    const { canonicalizeCombo } = await loadCombo('Win32')

    expect(canonicalizeCombo('ctrl+tab')).toBe('mod+tab')
    expect(canonicalizeCombo('ctrl+shift+tab')).toBe('mod+shift+tab')
    // Non-ctrl combos are unchanged.
    expect(canonicalizeCombo('mod+k')).toBe('mod+k')
  })
})

describe('formatCombo — honest Control labels', () => {
  it('renders the Control glyph on macOS', async () => {
    const { formatCombo } = await loadCombo('MacIntel')

    expect(formatCombo('ctrl+tab')).toBe('⌃⇥')
    expect(formatCombo('ctrl+shift+tab')).toBe('⌃⇧⇥')
  })

  it('renders "Ctrl+…" off macOS (base key keeps its glyph)', async () => {
    const { formatCombo } = await loadCombo('Win32')

    expect(formatCombo('ctrl+tab')).toBe('Ctrl+⇥')
    expect(formatCombo('ctrl+shift+tab')).toBe('Ctrl+Shift+⇥')
  })
})

describe('comboAllowedInInput', () => {
  it('lets ctrl combos fire while typing (e.g. ⌃Tab from the composer)', async () => {
    const { comboAllowedInInput } = await loadCombo('MacIntel')

    expect(comboAllowedInInput('ctrl+tab')).toBe(true)
    expect(comboAllowedInInput('ctrl+shift+tab')).toBe(true)
    expect(comboAllowedInInput('mod+k')).toBe(true)
    expect(comboAllowedInInput('shift+x')).toBe(false)
  })
})

// AIS-798: one shortcut logic for every OS — `mod` is ⌘ on macOS and Ctrl on
// Windows/Linux, and each OS shows its own labels.
const PLATFORMS = [
  { name: 'macOS', platform: 'MacIntel', mac: true },
  { name: 'Windows', platform: 'Win32', mac: false },
  { name: 'Linux', platform: 'Linux x86_64', mac: false }
] as const

describe.each(PLATFORMS)('Mod mapping on $name', ({ mac, platform }) => {
  it('treats only the OS accelerator as Mod', async () => {
    const { isModKey } = await loadCombo(platform)

    expect(isModKey({ ctrlKey: false, metaKey: true })).toBe(mac)
    expect(isModKey({ ctrlKey: true, metaKey: false })).toBe(!mac)
    expect(isModKey({ ctrlKey: false, metaKey: false })).toBe(false)
  })

  it('reads the accelerator chord as "mod+…"', async () => {
    const { comboFromEvent } = await loadCombo(platform)
    const accelerator = mac ? { metaKey: true } : { ctrlKey: true }

    expect(comboFromEvent(keydown({ code: 'KeyK', ...accelerator }))).toBe('mod+k')
    expect(comboFromEvent(keydown({ code: 'KeyK', shiftKey: true, ...accelerator }))).toBe('mod+shift+k')
    expect(comboFromEvent(keydown({ code: 'Digit1', altKey: true, ...accelerator }))).toBe('mod+alt+1')
  })
})

describe('the Windows/Super key is never Mod', () => {
  it.each(['Win32', 'Linux x86_64'])('keeps Win/Super as "meta" on %s', async platform => {
    const { comboFromEvent } = await loadCombo(platform)

    expect(comboFromEvent(keydown({ code: 'KeyK', metaKey: true }))).toBe('meta+k')
    // Win+Shift+N must not read as the bare `shift+n` (new chat).
    expect(comboFromEvent(keydown({ code: 'KeyN', metaKey: true, shiftKey: true }))).toBe('meta+shift+n')
  })

  it('labels it per OS', async () => {
    expect((await loadCombo('Win32')).formatCombo('meta+k')).toBe('Win+K')
    expect((await loadCombo('Linux x86_64')).formatCombo('meta+k')).toBe('Super+K')
  })
})

describe.each(PLATFORMS)('shortcut labels on $name', ({ mac, platform }) => {
  const expected: Array<[string, string, string]> = [
    // combo, macOS label, Windows/Linux label
    ['mod+k', '⌘K', 'Ctrl+K'],
    ['mod+n', '⌘N', 'Ctrl+N'],
    ['mod+shift+k', '⌘⇧K', 'Ctrl+Shift+K'],
    ['mod+alt+1', '⌘⌥1', 'Ctrl+Alt+1'],
    ['mod+enter', '⌘↵', 'Ctrl+↵'],
    ['mod+/', '⌘/', 'Ctrl+/'],
    ['shift+enter', '⇧↵', 'Shift+↵'],
    ['escape', 'Esc', 'Esc']
  ]

  it.each(expected)('formats %s', async (combo, macLabel, otherLabel) => {
    const { formatCombo } = await loadCombo(platform)

    expect(formatCombo(combo)).toBe(mac ? macLabel : otherLabel)
  })

  it('splits labels into one key cap per token', async () => {
    const { comboTokens } = await loadCombo(platform)

    expect(comboTokens('mod+shift+k')).toEqual(mac ? ['⌘', '⇧', 'K'] : ['Ctrl', 'Shift', 'K'])
  })

  it('never shows the other OS’s modifiers for a shipped binding', async () => {
    const { canonicalizeCombo, formatCombo } = await loadCombo(platform)
    const { KEYBIND_ACTIONS, KEYBIND_READONLY } = await import('./actions')
    const combos = [...KEYBIND_ACTIONS.flatMap(action => action.defaults), ...KEYBIND_READONLY.flatMap(r => r.keys)]

    for (const combo of combos) {
      const label = formatCombo(canonicalizeCombo(combo))

      if (mac) {
        expect(label, combo).not.toMatch(/Ctrl|Alt|Shift|Win|Super/)
      } else {
        expect(label, combo).not.toMatch(/[⌘⌥⇧⌃]/)
      }
    }
  })
})
