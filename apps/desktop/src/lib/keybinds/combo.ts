// Keybind combo normalization + display.
//
// A combo is a canonical lowercase string like "mod+k", "mod+shift+]", "shift+x",
// or "r". `mod` is Cmd on macOS / Ctrl elsewhere, so a single binding works on
// both. We derive the base key from `event.code` (not `event.key`) so Shift never
// mutates it ("shift+/" stays "shift+/" instead of becoming "shift+?").
//
// `ctrl` is physical Control, distinct from `mod`. It only matters on macOS,
// where `mod` is Cmd and Cmd+Tab is OS-reserved — so `ctrl+tab` is literally
// Control+Tab. Off macOS, Control already *is* `mod`, so `canonicalizeCombo`
// folds `ctrl` → `mod`.
//
// `meta` is the Windows / Super key off macOS. It is never `mod` (AIS-798):
// Win+K must not fire the ⌘K palette, and Win+Shift+N must not read as the
// bare `shift+n`.

const PLATFORM_HINT = typeof navigator !== 'undefined' ? navigator.platform || navigator.userAgent || '' : ''

export const IS_MAC = /mac/i.test(PLATFORM_HINT)
const IS_WINDOWS = /win/i.test(PLATFORM_HINT)

// The primary accelerator of a key or pointer event — ⌘ on macOS, Ctrl on
// Windows/Linux (AIS-798). Every hand-written shortcut check goes through this
// so one gesture means the same thing on every OS (Control on macOS stays the
// separate `ctrl` modifier; Windows' Win key never counts).
export function isModKey(event: Pick<KeyboardEvent, 'ctrlKey' | 'metaKey'>): boolean {
  return IS_MAC ? event.metaKey : event.ctrlKey
}

// event.code → canonical base token. Letters/digits map to their lowercase
// character; everything else uses an explicit name so combos read cleanly.
const CODE_TO_KEY: Record<string, string> = {
  Backquote: '`',
  Backslash: '\\',
  BracketLeft: '[',
  BracketRight: ']',
  Comma: ',',
  Equal: '=',
  Minus: '-',
  Period: '.',
  Quote: "'",
  Semicolon: ';',
  Slash: '/',
  Space: 'space',
  Enter: 'enter',
  Escape: 'escape',
  Backspace: 'backspace',
  Tab: 'tab',
  ArrowUp: 'up',
  ArrowDown: 'down',
  ArrowLeft: 'left',
  ArrowRight: 'right'
}

const MODIFIER_CODES = new Set([
  'AltLeft',
  'AltRight',
  'ControlLeft',
  'ControlRight',
  'MetaLeft',
  'MetaRight',
  'ShiftLeft',
  'ShiftRight'
])

function baseKeyFromCode(code: string): string | null {
  if (code.startsWith('Key')) {
    return code.slice(3).toLowerCase()
  }

  if (code.startsWith('Digit')) {
    return code.slice(5)
  }

  if (code.startsWith('Numpad')) {
    const rest = code.slice(6)

    return /^[0-9]$/.test(rest) ? rest : null
  }

  if (code.startsWith('F') && /^F\d{1,2}$/.test(code)) {
    return code.toLowerCase()
  }

  return CODE_TO_KEY[code] ?? null
}

// Returns the canonical combo for a keydown, or null while only modifiers are
// held (so capture mode keeps waiting for a real key).
export function comboFromEvent(event: KeyboardEvent): string | null {
  if (MODIFIER_CODES.has(event.code)) {
    return null
  }

  const base = baseKeyFromCode(event.code)

  if (!base) {
    return null
  }

  const parts: string[] = []

  // macOS reports Cmd (`mod`) and Control (`ctrl`) separately; elsewhere
  // Control IS the accelerator, so it folds into `mod`, and the Windows/Super
  // key stays its own `meta`.
  if (isModKey(event)) {
    parts.push('mod')
  }

  if (event.ctrlKey && IS_MAC) {
    parts.push('ctrl')
  }

  if (event.metaKey && !IS_MAC) {
    parts.push('meta')
  }

  if (event.altKey) {
    parts.push('alt')
  }

  if (event.shiftKey) {
    parts.push('shift')
  }

  parts.push(base)

  return parts.join('+')
}

// Rewrites a binding to the form `comboFromEvent` emits, so it indexes under
// the same key a live keypress produces. Off macOS, `ctrl+…` and `mod+…` are
// the one Control chord, so a shipped `ctrl+tab` matches a real Control+Tab.
export function canonicalizeCombo(combo: string): string {
  return IS_MAC ? combo : combo.replace(/\bctrl\b/g, 'mod')
}

const TOKEN_LABELS: Record<string, string> = {
  enter: '↵',
  escape: 'Esc',
  backspace: '⌫',
  tab: '⇥',
  space: 'Space',
  up: '↑',
  down: '↓',
  left: '←',
  right: '→'
}

function labelForBase(base: string): string {
  if (TOKEN_LABELS[base]) {
    return TOKEN_LABELS[base]
  }

  if (/^f\d{1,2}$/.test(base)) {
    return base.toUpperCase()
  }

  return base.length === 1 ? base.toUpperCase() : base
}

function labelForMod(mod: string): string {
  if (mod === 'mod') {
    return IS_MAC ? '⌘' : 'Ctrl'
  }

  if (mod === 'ctrl') {
    return IS_MAC ? '⌃' : 'Ctrl'
  }

  if (mod === 'alt') {
    return IS_MAC ? '⌥' : 'Alt'
  }

  if (mod === 'shift') {
    return IS_MAC ? '⇧' : 'Shift'
  }

  if (mod === 'meta') {
    return IS_MAC ? '⌘' : IS_WINDOWS ? 'Win' : 'Super'
  }

  return mod
}

// Per-key display tokens, e.g. ["⌘", "K"] on macOS, ["Ctrl", "K"] elsewhere —
// one cap per token for <KbdGroup>.
export function comboTokens(combo: string): string[] {
  const parts = combo.split('+')
  const base = parts.pop() ?? ''

  return [...parts.map(labelForMod), labelForBase(base)]
}

// Human-readable label, e.g. "⌘⇧K" on macOS, "Ctrl+Shift+K" elsewhere.
export function formatCombo(combo: string): string {
  const tokens = comboTokens(combo)

  return IS_MAC ? tokens.join('') : tokens.join('+')
}

// True when focus is in a text-entry surface, so bare-key shortcuts don't fire
// while the user is typing.
export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null

  return Boolean(
    el?.isContentEditable ||
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement
  )
}

// A primary modifier (Cmd/Ctrl/Control) fires even while typing (e.g. ⌘K or
// ⌃Tab from the composer); bare/Shift-only combos are suppressed in inputs.
export function comboAllowedInInput(combo: string): boolean {
  return /^(?:mod|ctrl)(?:\+|$)/.test(combo)
}
