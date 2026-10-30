/**
 * Built-in desktop themes. Names match the CLI skins / dashboard presets.
 * Add new themes here — no code changes needed elsewhere.
 */

import type { DesktopTheme, DesktopThemeTypography } from './types'

// Color-emoji fonts to append to every stack as a last resort. None of the UI
// text/mono fonts carry emoji glyphs, so without this emoji render as tofu
// boxes on platforms whose default text font lacks them (e.g. Linux/#40364).
// Covers macOS, Windows, Linux, plus the `emoji` generic for anything else.
export const EMOJI_FALLBACK =
  '"Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji", emoji'

// AIS-798: the bundled UI faces (src/styles.css imports them; SIL OFL 1.1).
// One face per role on every OS, so text measures and wraps the same on
// Windows, macOS and Linux. The system stack behind them only fills scripts
// the bundled fonts lack (CJK, Arabic, …). Built-in themes never load fonts
// from the network.
export const BUNDLED_SANS_FAMILY = '"Inter Variable"'
export const BUNDLED_MONO_FAMILY = '"JetBrains Mono Variable"'

const UI_SANS = `${BUNDLED_SANS_FAMILY}, system-ui, sans-serif, ${EMOJI_FALLBACK}`

const UI_MONO = `${BUNDLED_MONO_FAMILY}, ui-monospace, monospace, ${EMOJI_FALLBACK}`

export const DEFAULT_TYPOGRAPHY: DesktopThemeTypography = { fontSans: UI_SANS, fontMono: UI_MONO }

// AIMDS Suite accents (AIS-794): the Suite's blue, its stronger hover/focus
// step, and the darker step light surfaces need for readable white text.
const SUITE_ACCENT = '#4D7CFF'
const SUITE_ACCENT_STRONG = '#6C95FF'
const SUITE_ACCENT_ON_LIGHT = '#3A68F5'

/**
 * Nous — canonical AIMDS Suite identity (AIS-794): the Suite's navy page
 * (#0B1020) under a soft top-left blue glow, #161D3D cards on #232C54
 * hairlines, #E7ECFF text with #98A2C8 muted copy and the #4D7CFF accent.
 * The light variant keeps the same hues on a pale blue-white page. The
 * palette is painted as given (`exactPalette`): no neutral grey mixed into
 * the surfaces, so the desktop matches the Suite pixel for pixel.
 */
export const nousTheme: DesktopTheme = {
  name: 'nous',
  label: 'AIMDS Suite',
  description: 'AIMDS Suite navy with a blue glow and blue accents',
  exactPalette: true,
  colors: {
    background: '#F5F7FF',
    foreground: '#0F1633',
    card: '#FFFFFF',
    cardForeground: '#0F1633',
    muted: '#EEF1FB',
    mutedForeground: '#5C668F',
    popover: '#FFFFFF',
    popoverForeground: '#0F1633',
    primary: SUITE_ACCENT_ON_LIGHT,
    primaryForeground: '#FFFFFF',
    secondary: '#E8EEFF',
    secondaryForeground: '#1D2A66',
    accent: '#E1E9FF',
    accentForeground: '#0F1633',
    border: '#DDE3F4',
    input: '#CAD3EE',
    ring: SUITE_ACCENT,
    midground: SUITE_ACCENT_ON_LIGHT,
    composerRing: SUITE_ACCENT_ON_LIGHT,
    destructive: '#E11D48',
    destructiveForeground: '#FFFFFF',
    sidebarBackground: '#EEF2FD',
    sidebarBorder: '#DDE3F4',
    userBubble: '#E8EEFF',
    userBubbleBorder: '#CFDBFF',
    glow: '#DCE5FF'
  },
  darkColors: {
    background: '#0B1020',
    foreground: '#E7ECFF',
    card: '#161D3D',
    cardForeground: '#E7ECFF',
    muted: '#121937',
    mutedForeground: '#98A2C8',
    popover: '#161D3D',
    popoverForeground: '#E7ECFF',
    primary: SUITE_ACCENT,
    primaryForeground: '#FFFFFF',
    secondary: '#1B2450',
    secondaryForeground: '#E7ECFF',
    accent: '#1E2A5E',
    accentForeground: '#E7ECFF',
    border: '#232C54',
    input: '#2C3766',
    ring: SUITE_ACCENT_STRONG,
    midground: SUITE_ACCENT,
    composerRing: SUITE_ACCENT,
    destructive: '#F43F5E',
    destructiveForeground: '#FFFFFF',
    sidebarBackground: '#0D1328',
    sidebarBorder: '#232C54',
    userBubble: '#161D3D',
    userBubbleBorder: '#232C54',
    glow: '#1B2761'
  },
  typography: DEFAULT_TYPOGRAPHY
}

/** Deep blue-violet with cool accents. Matches the dashboard midnight theme. */
export const midnightTheme: DesktopTheme = {
  name: 'midnight',
  label: 'Midnight',
  description: 'Deep blue-violet with cool accents',
  colors: {
    background: '#08081c',
    foreground: '#ddd6ff',
    card: '#0d0d28',
    cardForeground: '#ddd6ff',
    muted: '#13133a',
    mutedForeground: '#7c7ab0',
    popover: '#0f0f2e',
    popoverForeground: '#ddd6ff',
    primary: '#ddd6ff',
    primaryForeground: '#08081c',
    secondary: '#1a1a4a',
    secondaryForeground: '#c4bff0',
    accent: '#1a1a44',
    accentForeground: '#d0c8ff',
    border: '#1e1e52',
    input: '#1e1e52',
    ring: '#8b80e8',
    midground: '#8b80e8',
    destructive: '#b03060',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#06061a',
    sidebarBorder: '#12123a',
    userBubble: '#14143a',
    userBubbleBorder: '#242466'
  },
  typography: DEFAULT_TYPOGRAPHY
}

/** Warm crimson and bronze — forge vibes. Matches the CLI ares skin. */
export const emberTheme: DesktopTheme = {
  name: 'ember',
  label: 'Ember',
  description: 'Warm crimson and bronze — forge vibes',
  colors: {
    background: '#160800',
    foreground: '#ffd8b0',
    card: '#1e0e04',
    cardForeground: '#ffd8b0',
    muted: '#2a1408',
    mutedForeground: '#aa7a56',
    popover: '#221008',
    popoverForeground: '#ffd8b0',
    primary: '#ffd8b0',
    primaryForeground: '#160800',
    secondary: '#341800',
    secondaryForeground: '#f0c090',
    accent: '#301600',
    accentForeground: '#e8c080',
    border: '#3a1c08',
    input: '#3a1c08',
    ring: '#d97316',
    midground: '#d97316',
    destructive: '#c43010',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#100600',
    sidebarBorder: '#2a1004',
    userBubble: '#2a1000',
    userBubbleBorder: '#4a2010'
  },
  typography: DEFAULT_TYPOGRAPHY
}

/** Clean grayscale. Matches the CLI mono skin and dashboard mono theme. */
export const monoTheme: DesktopTheme = {
  name: 'mono',
  label: 'Mono',
  description: 'Clean grayscale — minimal and focused',
  colors: {
    background: '#0e0e0e',
    foreground: '#eaeaea',
    card: '#141414',
    cardForeground: '#eaeaea',
    muted: '#1e1e1e',
    mutedForeground: '#808080',
    popover: '#181818',
    popoverForeground: '#eaeaea',
    primary: '#eaeaea',
    primaryForeground: '#0e0e0e',
    secondary: '#262626',
    secondaryForeground: '#c8c8c8',
    accent: '#222222',
    accentForeground: '#d8d8d8',
    border: '#2a2a2a',
    input: '#2a2a2a',
    ring: '#9a9a9a',
    midground: '#9a9a9a',
    destructive: '#a84040',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#0a0a0a',
    sidebarBorder: '#202020',
    userBubble: '#1a1a1a',
    userBubbleBorder: '#363636'
  }
}

/** Neon green on black. Matches the CLI cyberpunk skin and dashboard theme. */
export const cyberpunkTheme: DesktopTheme = {
  name: 'cyberpunk',
  label: 'Cyberpunk',
  description: 'Neon green on black — matrix terminal',
  colors: {
    background: '#000a00',
    foreground: '#00ff41',
    card: '#001200',
    cardForeground: '#00ff41',
    muted: '#001a00',
    mutedForeground: '#1a8a30',
    popover: '#001000',
    popoverForeground: '#00ff41',
    primary: '#00ff41',
    primaryForeground: '#000a00',
    secondary: '#002800',
    secondaryForeground: '#00cc34',
    accent: '#002000',
    accentForeground: '#00e038',
    border: '#003000',
    input: '#003000',
    ring: '#00ff41',
    midground: '#00ff41',
    destructive: '#ff003c',
    destructiveForeground: '#000a00',
    sidebarBackground: '#000600',
    sidebarBorder: '#001800',
    userBubble: '#001400',
    userBubbleBorder: '#004800'
  },
  // All-mono look, with the bundled mono so it reads the same on every OS.
  typography: { fontMono: UI_MONO, fontSans: UI_MONO }
}

/** Cool slate blue for developers. Matches the CLI slate skin. */
export const slateTheme: DesktopTheme = {
  name: 'slate',
  label: 'Slate',
  description: 'Cool slate blue — focused developer theme',
  colors: {
    background: '#0d1117',
    foreground: '#c9d1d9',
    card: '#161b22',
    cardForeground: '#c9d1d9',
    muted: '#21262d',
    mutedForeground: '#8b949e',
    popover: '#1c2128',
    popoverForeground: '#c9d1d9',
    primary: '#c9d1d9',
    primaryForeground: '#0d1117',
    secondary: '#2a3038',
    secondaryForeground: '#adb5bf',
    accent: '#1e2530',
    accentForeground: '#c0c8d0',
    border: '#30363d',
    input: '#30363d',
    ring: '#58a6ff',
    midground: '#58a6ff',
    destructive: '#cf4848',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#090d13',
    sidebarBorder: '#1c2228',
    userBubble: '#1e2a38',
    userBubbleBorder: '#2e4060'
  },
  typography: DEFAULT_TYPOGRAPHY
}

export const BUILTIN_THEMES: Record<string, DesktopTheme> = {
  nous: nousTheme,
  midnight: midnightTheme,
  ember: emberTheme,
  mono: monoTheme,
  cyberpunk: cyberpunkTheme,
  slate: slateTheme
}

export const BUILTIN_THEME_LIST = Object.values(BUILTIN_THEMES)

/** Skin used when nothing is persisted or the persisted name is retired. */
export const DEFAULT_SKIN_NAME = 'nous'
