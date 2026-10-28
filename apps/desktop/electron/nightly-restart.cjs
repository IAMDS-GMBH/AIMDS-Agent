'use strict'

// AIS-428: the desktop restarts its local backend once a night so new code
// (updates pulled by `hermes update`, fixed catalog MCP servers) is loaded
// without the user quitting the app. The dashboard process keeps whatever it
// imported at start; a stuck session store or MCP server also clears.
//
// Rules (pure, so they are testable without Electron):
// - one restart per night at RESTART_TIME (local, default 03:30)
// - a machine that slept through that time restarts shortly after wake
// - never while a chat turn or cron job runs — try again later
// - only after the system has been awake for a while (AIS-749, below)
// - HERMES_NIGHTLY_RESTART=off disables it, HH:MM moves it
//
// AIS-749: a Mac on battery wakes for a few seconds now and then without the
// user (DarkWake). Timers fire in such a wake, the restart ran, and the Mac
// went back to sleep a second later with the new backend half started. A
// restart therefore needs an uninterrupted awake window of MIN_AWAKE_MS:
// - after a powerMonitor 'suspend' only a 'resume' (a real wake) opens one;
// - a sleep the app was not told about shows up as the wall clock running
//   ahead of the monotonic clock (which stops while suspended) and restarts
//   the window.

const DEFAULT_RESTART_TIME = '03:30'
const RESUME_DELAY_MS = 30 * 1000
const BUSY_RETRY_MS = 10 * 60 * 1000
const CHECK_INTERVAL_MS = 60 * 1000
// Dark wakes last seconds; below RESUME_DELAY_MS so a real wake is not held up.
const MIN_AWAKE_MS = 20 * 1000
// Wall clock ahead of the monotonic clock by more than this = the machine slept.
const SLEEP_DRIFT_MS = 30 * 1000

function parseRestartTime(raw) {
  const text = String(raw == null ? '' : raw).trim().toLowerCase()
  if (!text) return parseRestartTime(DEFAULT_RESTART_TIME)
  if (['off', '0', 'false', 'no', 'disabled'].includes(text)) return null
  const match = /^(\d{1,2}):(\d{2})$/.exec(text)
  if (!match) return parseRestartTime(DEFAULT_RESTART_TIME)
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (hours > 23 || minutes > 59) return parseRestartTime(DEFAULT_RESTART_TIME)
  return { hours, minutes }
}

// The most recent scheduled restart moment at or before `now`.
function lastScheduledBefore(now, time) {
  const at = new Date(now)
  at.setHours(time.hours, time.minutes, 0, 0)
  if (at.getTime() > now.getTime()) at.setDate(at.getDate() - 1)
  return at
}

// A restart is due when a scheduled moment passed since the backend started
// (or since the last restart).
function restartDue(now, time, lastStartedAt) {
  if (!time || !lastStartedAt) return false
  return lastScheduledBefore(now, time).getTime() > lastStartedAt.getTime()
}

function createNightlyRestarter({
  restartTime = process.env.HERMES_NIGHTLY_RESTART,
  now = () => new Date(),
  monotonic = () => performance.now(),
  isIdle,
  restart,
  log = () => {},
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  setRepeat = setInterval
} = {}) {
  const time = parseRestartTime(restartTime)
  let lastStartedAt = now()
  let pending = null
  let inFlight = false
  // Start of the current awake window, null while suspended.
  let awakeSince = { wall: now().getTime(), mono: monotonic() }

  function markAwake() {
    awakeSince = { wall: now().getTime(), mono: monotonic() }
  }

  // True once the system has been awake without interruption for MIN_AWAKE_MS.
  function awakeLongEnough() {
    if (!awakeSince) return false
    const awakeMs = monotonic() - awakeSince.mono
    const wallMs = now().getTime() - awakeSince.wall
    if (wallMs - awakeMs > SLEEP_DRIFT_MS) {
      log(`[nightly] the system slept since ${new Date(awakeSince.wall).toISOString()}; waiting for it to stay awake`)
      markAwake()
      return false
    }
    return awakeMs >= MIN_AWAKE_MS
  }

  async function attempt(reason) {
    pending = null
    if (inFlight || !restartDue(now(), time, lastStartedAt)) return false
    // Deferred, not dropped: the interval check tries again once awake.
    if (!awakeLongEnough()) return false
    inFlight = true
    try {
      let idle = false
      try {
        idle = await isIdle()
      } catch (error) {
        log(`[nightly] idle check failed (${error?.message || error}); retrying later`)
      }
      if (!idle) {
        log('[nightly] backend busy (chat turn or cron job running); retrying in 10 min')
        schedule(BUSY_RETRY_MS, reason)
        return false
      }
      log(`[nightly] restarting the backend to load new code (${reason})`)
      await restart()
      lastStartedAt = now()
      return true
    } finally {
      inFlight = false
    }
  }

  function tick() {
    if (!pending && restartDue(now(), time, lastStartedAt)) void attempt('scheduled')
  }

  function schedule(delayMs, reason) {
    if (pending) clearTimer(pending)
    pending = setTimer(() => attempt(reason), delayMs)
  }

  return {
    enabled: Boolean(time),
    // The backend (re)started for another reason — that counts as tonight's.
    noteBackendStarted() {
      lastStartedAt = now()
    },
    onSuspend() {
      awakeSince = null
      if (pending) clearTimer(pending)
      pending = null
    },
    onResume() {
      markAwake()
      if (restartDue(now(), time, lastStartedAt)) schedule(RESUME_DELAY_MS, 'missed while asleep')
    },
    start() {
      if (!time) {
        log('[nightly] backend restart disabled (HERMES_NIGHTLY_RESTART=off)')
        return null
      }
      return setRepeat(tick, CHECK_INTERVAL_MS)
    },
    tick,
    attempt
  }
}

module.exports = {
  BUSY_RETRY_MS,
  DEFAULT_RESTART_TIME,
  MIN_AWAKE_MS,
  RESUME_DELAY_MS,
  SLEEP_DRIFT_MS,
  createNightlyRestarter,
  lastScheduledBefore,
  parseRestartTime,
  restartDue
}
