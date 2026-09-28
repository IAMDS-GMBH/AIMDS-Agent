// AIS-352: pure decision helpers for the primary-backend boot chain in
// main.cjs — kept out of main.cjs so they can be unit-tested.
//
// What went wrong on 2026-09-16: the backend needed longer than 45 s to bind
// (it was waiting on a macOS permission dialog), the readiness wait gave up,
// and the retry mistook the still-starting child for a stale backend and
// SIGTERMed it. Meanwhile the exit handler of that child nulled the
// single-flight promise, so a second, concurrent boot chain started and the
// two chains kept killing each other's backend.

// Local backend readiness. The first start after an update may compile
// bytecode and wait for OS prompts — 45 s was far too short.
const LOCAL_READY_TIMEOUT_MS = 180_000
// A remote gateway either answers or is down; keep the short deadline.
const REMOTE_READY_TIMEOUT_MS = 45_000
// Cadence of the "still waiting" boot-stage updates.
const READY_SLOW_INTERVAL_MS = 10_000
const READY_POLL_MS = 500

/**
 * Only the CURRENT child may clear the process handle / single-flight state.
 * A stale child from an earlier attempt exiting later must not touch the live
 * one (that is how a healthy backend survived on 9120 into the next boot).
 */
function shouldClearConnectionState({ exitingChild, currentChild }) {
  return currentChild == null || exitingChild === currentChild
}

/**
 * AIS-430: the single-flight promise belongs to the boot chain that created
 * it. A backend that had been ready may drop it on exit only while it is
 * still that chain's promise. The nightly restart clears the handle before
 * the old backend exits; the renderer meanwhile starts the next chain, and
 * the old backend's exit used to null that chain's promise — the reload then
 * started a second chain whose backend bound another port, and one of the two
 * backends was left running forever.
 */
function shouldDropSingleFlight({ backendReady, ownConnection, currentConnection }) {
  return Boolean(backendReady) && ownConnection != null && currentConnection === ownConnection
}

function isAlive(child) {
  return Boolean(child) && !child.killed && child.exitCode == null && child.signalCode == null
}

/**
 * AIS-430: every primary backend this process spawned, with the boot chain
 * that spawned it. A backend whose chain is no longer the current one is a
 * stray — nothing references it any more, so it is stopped instead of
 * running (with its own MCP servers and cron ticker) until the app quits.
 */
function createBackendRegistry() {
  const entries = new Map()
  return {
    add(child, chain) {
      if (!child) return
      entries.set(child, chain)
      if (typeof child.once === 'function') child.once('exit', () => entries.delete(child))
    },
    // Live backends that belong to a chain other than `currentChain` (all of
    // them when `currentChain` is null), never `keep`.
    strays({ currentChain = null, keep = null } = {}) {
      const out = []
      for (const [child, chain] of entries) {
        if (child === keep || !isAlive(child)) continue
        if (currentChain == null || chain !== currentChain) out.push(child)
      }
      return out
    },
    size() {
      return entries.size
    }
  }
}

// How long after a scheduled (nightly) restart a boot that needed a second
// attempt is expected churn rather than a boot problem worth an incident.
const SCHEDULED_RESTART_QUIET_MS = 2 * 60 * 1000

function isScheduledRestartRecovery({ now, scheduledRestartAt, quietMs = SCHEDULED_RESTART_QUIET_MS }) {
  return Number.isFinite(scheduledRestartAt) && now >= scheduledRestartAt && now - scheduledRestartAt <= quietMs
}

/**
 * Poll `probe()` until it resolves. Calls `onSlow(elapsedMs)` every
 * `slowIntervalMs` so the boot overlay can say why nothing happens yet.
 * Rejects after `timeoutMs` with the last probe error in the message — the
 * same text shape the incident classifier keys on ("did not become ready").
 */
async function waitForBackendReady({
  probe,
  timeoutMs = LOCAL_READY_TIMEOUT_MS,
  slowIntervalMs = READY_SLOW_INTERVAL_MS,
  pollMs = READY_POLL_MS,
  onSlow = () => {},
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  now = () => Date.now()
}) {
  const started = now()
  const deadline = started + timeoutMs
  let nextSlowAt = started + slowIntervalMs
  let lastError = null
  while (now() < deadline) {
    try {
      await probe()
      return { waited_ms: now() - started }
    } catch (error) {
      lastError = error
    }
    if (now() >= nextSlowAt) {
      try {
        onSlow(now() - started)
      } catch {
        void 0
      }
      nextSlowAt = now() + slowIntervalMs
    }
    await sleep(pollMs)
  }
  const error = new Error(`Hermes backend did not become ready: ${lastError?.message || 'timeout'}`)
  error.code = 'backend-not-ready'
  error.waited_ms = now() - started
  throw error
}

/** Human line for the slow-boot stage. */
function slowBootMessage(elapsedMs) {
  const seconds = Math.max(1, Math.round(elapsedMs / 1000))
  return `Still waiting for the Hermes backend (${seconds} s) — the first start after an update can take a while`
}

module.exports = {
  LOCAL_READY_TIMEOUT_MS,
  READY_POLL_MS,
  READY_SLOW_INTERVAL_MS,
  REMOTE_READY_TIMEOUT_MS,
  SCHEDULED_RESTART_QUIET_MS,
  createBackendRegistry,
  isScheduledRestartRecovery,
  shouldClearConnectionState,
  shouldDropSingleFlight,
  slowBootMessage,
  waitForBackendReady
}
