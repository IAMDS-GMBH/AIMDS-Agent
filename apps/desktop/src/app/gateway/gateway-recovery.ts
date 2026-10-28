import type { DesktopBootProgress } from '@/global'

// AIS-749: pure decisions for useGatewayBoot's recovery paths, kept out of the
// hook so they can be unit-tested.
//
// What went wrong on 2026-10-28: the nightly restart reloaded the window in a
// dark wake, the Mac slept mid-boot, and the main process's readiness wait
// gave up 17 min later. The renderer's initial boot() failed with it. A
// second boot chain in main came up 2 s later, but the renderer only ever
// reconnected AFTER a completed boot: every wake/online/visibility signal
// returned early on `!bootCompleted`, and the main process's later boot steps
// cleared the error, so the window sat on CONNECTING until the app was
// restarted.

/** Bounds for the awaited IPC steps of a reconnect (AIS-749): none may hold the
 *  single in-flight attempt forever. A timed-out step is retried by the
 *  backoff; a retry joins the main process's boot chain, it does not start a
 *  second one. */
export const RECOVERY_REVALIDATE_TIMEOUT_MS = 10_000
export const RECOVERY_CONNECTION_TIMEOUT_MS = 60_000
export const RECOVERY_WS_URL_TIMEOUT_MS = 30_000

export type RecoveryAction = 'boot' | 'none' | 'reconnect' | 'refresh'

export interface RecoveryState {
  cancelled: boolean
  /** The initial boot (or a re-run of it) completed. */
  bootCompleted: boolean
  /** A boot() run is in flight. */
  booting: boolean
  /** The in-flight boot() run outlived its watchdog. */
  bootStalled: boolean
  gatewayOpen: boolean
  refreshPending: boolean
}

/**
 * What a wake signal (power resume, network online, window visible) or a
 * "backend is ready" report from the main process should do.
 *
 * - Before a completed boot: (re)run the boot unless one is in flight and
 *   still within its watchdog. A failed boot is no dead end.
 * - After it: reconnect a socket that is not open, or retry a pending
 *   settings/sessions refresh on an open one.
 */
export function recoveryAction(state: RecoveryState): RecoveryAction {
  if (state.cancelled) {
    return 'none'
  }

  if (!state.bootCompleted) {
    return state.booting && !state.bootStalled ? 'none' : 'boot'
  }

  if (!state.gatewayOpen) {
    return 'reconnect'
  }

  return state.refreshPending ? 'refresh' : 'none'
}

/** The main process reports a backend that answered its readiness probe. */
export function isBackendReadyProgress(progress: Pick<DesktopBootProgress, 'error' | 'phase'> | null | undefined) {
  return progress?.phase === 'backend.ready' && !progress.error
}

export class RecoveryStepTimeout extends Error {}

/** Reject with RecoveryStepTimeout when `promise` has not settled after `ms`. */
export function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new RecoveryStepTimeout(message)), ms)

    promise.then(
      value => {
        clearTimeout(timer)
        resolve(value)
      },
      error => {
        clearTimeout(timer)
        reject(error)
      }
    )
  })
}
