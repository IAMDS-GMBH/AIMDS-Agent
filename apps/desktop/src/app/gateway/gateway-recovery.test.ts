import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  isBackendReadyProgress,
  recoveryAction,
  type RecoveryState,
  RecoveryStepTimeout,
  withTimeout
} from './gateway-recovery'

const base: RecoveryState = {
  cancelled: false,
  bootCompleted: true,
  booting: false,
  bootStalled: false,
  gatewayOpen: false,
  refreshPending: false
}

describe('recoveryAction (AIS-749)', () => {
  it('re-runs a failed initial boot instead of ignoring the signal', () => {
    expect(recoveryAction({ ...base, bootCompleted: false })).toBe('boot')
  })

  it('leaves a boot that is still within its watchdog alone', () => {
    expect(recoveryAction({ ...base, bootCompleted: false, booting: true })).toBe('none')
  })

  it('starts a fresh boot when the running one outlived its watchdog', () => {
    expect(recoveryAction({ ...base, bootCompleted: false, booting: true, bootStalled: true })).toBe('boot')
  })

  it('reconnects a closed socket after a completed boot', () => {
    expect(recoveryAction(base)).toBe('reconnect')
  })

  it('retries a pending refresh on an open socket, otherwise does nothing', () => {
    expect(recoveryAction({ ...base, gatewayOpen: true, refreshPending: true })).toBe('refresh')
    expect(recoveryAction({ ...base, gatewayOpen: true })).toBe('none')
  })

  it('does nothing once unmounted', () => {
    expect(recoveryAction({ ...base, cancelled: true, bootCompleted: false })).toBe('none')
  })
})

describe('isBackendReadyProgress', () => {
  it('matches only an error-free backend.ready report', () => {
    expect(isBackendReadyProgress({ phase: 'backend.ready', error: null })).toBe(true)
    expect(isBackendReadyProgress({ phase: 'backend.ready', error: 'boom' })).toBe(false)
    expect(isBackendReadyProgress({ phase: 'backend.error', error: null })).toBe(false)
    expect(isBackendReadyProgress(null)).toBe(false)
  })
})

describe('withTimeout', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('passes a settled value through', async () => {
    await expect(withTimeout(Promise.resolve(7), 1_000, 'slow')).resolves.toBe(7)
    await expect(withTimeout(Promise.reject(new Error('no')), 1_000, 'slow')).rejects.toThrow('no')
  })

  it('rejects a promise that never settles', async () => {
    vi.useFakeTimers()
    const pending = withTimeout(new Promise(() => undefined), 1_000, 'step timed out')
    const assertion = expect(pending).rejects.toBeInstanceOf(RecoveryStepTimeout)
    await vi.advanceTimersByTimeAsync(1_000)
    await assertion
  })
})
