import type { HermesConnection } from '@/global'

// Mirrors electron/titlebar.cjs: one frameless 34px band on every OS. macOS
// insets its traffic lights at the top left; Windows/Linux paint native
// min/max/close over the top-right corner (AIS-798).
export const TITLEBAR_HEIGHT = 34
export const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14
export const TITLEBAR_ICON_SIZE = 12
export const TITLEBAR_CONTROL_OFFSET_X = 74
export const TITLEBAR_CONTROL_HEIGHT = 22
export const TITLEBAR_CONTROLS_TOP = (TITLEBAR_HEIGHT - TITLEBAR_CONTROL_HEIGHT) / 2
export const TITLEBAR_FALLBACK_WINDOW_BUTTON_X = 24
// Edge inset used when no left-side native controls take up that space —
// Windows/Linux (native overlay is on the right) and macOS fullscreen
// (traffic lights are hidden). Matches the right-cluster's 0.75rem padding.
export const TITLEBAR_EDGE_INSET = 14

// Titlebar palette only. All sizing/radius/cursor/centering come from the
// shared <Button size="icon-titlebar"> (used polymorphically via asChild) —
// Button is the single source of button styling.
export const titlebarButtonClass =
  'text-muted-foreground/85 hover:bg-(--ui-control-hover-background) hover:text-foreground'

export const titlebarHeaderBaseClass =
  'pointer-events-none relative z-3 flex h-(--titlebar-height) shrink-0 items-center justify-start gap-3 border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-[max(0.75rem,var(--titlebar-content-inset,0rem))]'

export const titlebarHeaderShadowClass =
  "after:pointer-events-none after:absolute after:left-0 after:right-0 after:top-full after:h-4 after:bg-linear-to-b after:from-(--ui-chat-surface-background) after:to-transparent after:content-['']"

export function titlebarControlsPosition(
  windowButtonPosition: HermesConnection['windowButtonPosition'] | undefined,
  isFullscreen = false
) {
  const top = Math.max(0, TITLEBAR_CONTROLS_TOP)

  // No left-side native controls to dodge:
  //   - Windows/Linux: native min/max/close render on the right via titleBarOverlay.
  //   - macOS fullscreen: traffic lights are hidden.
  // In both cases, pin the cluster to the edge with a small inset.
  if (windowButtonPosition === null || isFullscreen) {
    return { left: TITLEBAR_EDGE_INSET, top }
  }

  return {
    left: (windowButtonPosition?.x ?? TITLEBAR_FALLBACK_WINDOW_BUTTON_X) + TITLEBAR_CONTROL_OFFSET_X,
    top
  }
}

// Upper bound for a measured native-controls width; anything wider is a bogus
// rect (e.g. before the first layout) and the reported constant wins.
const MAX_NATIVE_CONTROLS_WIDTH = 320

interface TitlebarAreaRect {
  width: number
  x: number
}

/**
 * Width of the OS window controls on the right edge, in CSS px.
 *
 * `reported` is the main process's constant (0 on macOS, whose controls sit on
 * the left). Windows/Linux measure the real cluster via the Window Controls
 * Overlay titlebar rect when it is available — the constant is wrong under
 * page zoom (the native buttons don't scale) and on Linux window managers
 * whose buttons are narrower or wider than Windows'.
 */
export function nativeControlsWidth(reported: number, rect: TitlebarAreaRect | null, viewportWidth: number): number {
  if (reported <= 0) {
    return 0
  }

  if (!rect || rect.width <= 0) {
    return reported
  }

  const measured = Math.round(viewportWidth - (rect.x + rect.width))

  return measured > 0 && measured <= MAX_NATIVE_CONTROLS_WIDTH ? measured : reported
}

/**
 * Top inset an element at the window edge needs so the OS controls never cover
 * it: the band height where the controls sit on the right (Windows/Linux),
 * zero on macOS where the traffic lights float over the drag strip.
 */
export function nativeControlsBand(controlsWidth: number): number {
  return controlsWidth > 0 ? TITLEBAR_HEIGHT : 0
}
