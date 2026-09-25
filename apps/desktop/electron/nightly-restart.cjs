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
// - HERMES_NIGHTLY_RESTART=off disables it, HH:MM moves it

const DEFAULT_RESTART_TIME = '03:30'
const RESUME_DELAY_MS = 30 * 1000
const BUSY_RETRY_MS = 10 * 60 * 1000
const CHECK_INTERVAL_MS = 60 * 1000

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

  async function attempt(reason) {
    pending = null
    if (inFlight || !restartDue(now(), time, lastStartedAt)) return false
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
    onResume() {
      if (restartDue(now(), time, lastStartedAt)) schedule(RESUME_DELAY_MS, 'missed while asleep')
    },
    start() {
      if (!time) {
        log('[nightly] backend restart disabled (HERMES_NIGHTLY_RESTART=off)')
        return null
      }
      return setRepeat(() => {
        if (!pending && restartDue(now(), time, lastStartedAt)) void attempt('scheduled')
      }, CHECK_INTERVAL_MS)
    },
    attempt
  }
}

module.exports = {
  BUSY_RETRY_MS,
  DEFAULT_RESTART_TIME,
  RESUME_DELAY_MS,
  createNightlyRestarter,
  lastScheduledBefore,
  parseRestartTime,
  restartDue
}
