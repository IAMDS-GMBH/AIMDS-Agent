// One title bar layout for every OS (AIS-798). No Electron imports, so
// `node --test` can pin the per-platform options directly; main.cjs feeds them
// into `new BrowserWindow(...)` and `setTitleBarOverlay(...)`.
//
// The layout: a frameless window with a 34px band the renderer paints itself
// (src/app/shell/titlebar.ts mirrors the constants). The OS keeps its native
// window controls — macOS traffic lights inset at the top left, Windows/Linux
// minimise/maximise/close painted over the top-right corner via
// `titleBarOverlay` — and the app toolbar clears them on whichever side they sit.

const TITLEBAR_HEIGHT = 34
const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14
const WINDOW_BUTTON_POSITION = Object.freeze({
  x: 24,
  y: TITLEBAR_HEIGHT / 2 - MACOS_TRAFFIC_LIGHTS_HEIGHT / 2
})
// Width Electron reserves for the Windows/Linux native min/max/close cluster
// when `titleBarOverlay` is enabled. The renderer measures the real width via
// the Window Controls Overlay API when it can and only falls back to this.
const NATIVE_OVERLAY_BUTTON_WIDTH = 144
// Overlay colours before the renderer reports its theme.
const FALLBACK_OVERLAY = Object.freeze({
  dark: { color: '#111111', symbolColor: '#f7f7f7' },
  light: { color: '#f7f7f7', symbolColor: '#242424' }
})

const isMacPlatform = platform => platform === 'darwin'

function isHexColor(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
}

// A renderer-supplied `{ background, foreground }` pair, or null when invalid.
function normalizeTitleBarTheme(payload) {
  if (!payload || !isHexColor(payload.background) || !isHexColor(payload.foreground)) {
    return null
  }

  return { background: payload.background, foreground: payload.foreground }
}

// `titleBarOverlay` for a window. macOS only reserves the band (the traffic
// lights carry their own colours); Windows/Linux tint the native controls to
// the app theme so the band reads as one surface.
function titleBarOverlayOptions(platform, { theme = null, useDarkColors = false } = {}) {
  if (isMacPlatform(platform)) {
    return { height: TITLEBAR_HEIGHT }
  }

  if (theme) {
    return { color: theme.background, height: TITLEBAR_HEIGHT, symbolColor: theme.foreground }
  }

  const fallback = useDarkColors ? FALLBACK_OVERLAY.dark : FALLBACK_OVERLAY.light

  return { color: fallback.color, height: TITLEBAR_HEIGHT, symbolColor: fallback.symbolColor }
}

// The window-chrome part of the BrowserWindow options, shared by the main
// window and standalone session windows so they never drift apart.
function windowChromeOptions(platform, overlayOptions = titleBarOverlayOptions(platform)) {
  const mac = isMacPlatform(platform)

  return {
    titleBarStyle: 'hidden',
    titleBarOverlay: overlayOptions,
    trafficLightPosition: mac ? { ...WINDOW_BUTTON_POSITION } : undefined,
    vibrancy: mac ? 'sidebar' : undefined
  }
}

// Width of the native controls on the right edge (0 on macOS, where they sit
// on the left and are reported as a window-button position instead).
function nativeOverlayWidth(platform) {
  return isMacPlatform(platform) ? 0 : NATIVE_OVERLAY_BUTTON_WIDTH
}

module.exports = {
  MACOS_TRAFFIC_LIGHTS_HEIGHT,
  NATIVE_OVERLAY_BUTTON_WIDTH,
  TITLEBAR_HEIGHT,
  WINDOW_BUTTON_POSITION,
  isHexColor,
  nativeOverlayWidth,
  normalizeTitleBarTheme,
  titleBarOverlayOptions,
  windowChromeOptions
}
