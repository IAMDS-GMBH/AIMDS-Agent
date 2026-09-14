// Pure helpers for the desktop's update *apply* hand-off (AIS-331).
//
// SUP-20260914-105316: a 0.7.3 client pressed "Update", the overlay showed
// "Handing off to the Hermes updater…" and then nothing — the desktop kept
// running, the updater never launched, and the overlay could not even be
// closed. Everything the main process does between that message and the
// actual `spawn()` used to be invisible: no catch, no `'error'` listener on
// the child, updater stdout/stderr thrown away, and a detached-HEAD git
// checkout asked to follow `main`. These helpers hold the decision logic so it
// can be tested without Electron; `main.cjs` wires them into `applyUpdates`.
const fs = require('node:fs')
const path = require('node:path')

const { isTagChannel, normalizeChannel } = require('./update-channels.cjs')

// File the updater's stdout/stderr are appended to. Lives next to the other
// `~/.hermes/logs/*.log` files so the support bundle ships it
// (hermes_cli/support_logs.py `_LOG_FILES`).
const UPDATER_LAUNCH_LOG = 'updater-launch.log'

// The channel `--branch` must carry for a *git* install, given what the
// checkout looks like.
//
// A release-managed install (marker present) already gets coerced by
// `effectiveUpdateChannel`; this only decides the git case. A detached HEAD
// (`git rev-parse --abbrev-ref HEAD` prints the literal "HEAD") is what every
// installer-produced checkout looks like: it was checked out at a tag, not on
// a branch. Sending `--branch main` there switches the checkout onto the
// source repo's main branch — the path AIS-323 demoted to an emergency
// fallback and one that a 0.7.3 `hermes update` cannot even satisfy when the
// origin is unreachable. Tag channels (stable/preview) resolve through the
// release repository instead, which is what such a client needs to get back
// onto a current version.
function resolveDetachedCheckoutChannel({ branch, marker, headRef }) {
  const normalized = normalizeChannel(branch || 'main')
  if (marker) return { branch: normalized, coerced: false }
  if (isTagChannel(normalized)) return { branch: normalized, coerced: false }
  const detached = typeof headRef === 'string' && headRef.trim() === 'HEAD'
  if (!detached) return { branch: normalized, coerced: false }
  return { branch: 'stable', coerced: true, from: normalized }
}

// Open the updater log for appending and build the `stdio` triple for
// `spawn()`. One descriptor serves both stdout and stderr so the file reads in
// wall-clock order. Falls back to `'ignore'` (the previous behaviour) when the
// file cannot be opened — a missing log must never block an update.
//
// The returned `close()` releases the parent's copy of the descriptor once
// `spawn()` has duplicated it into the child; the child keeps writing.
function openUpdaterLogStdio(logPath, { fsImpl = fs, now = () => new Date() } = {}) {
  let fd
  try {
    fsImpl.mkdirSync(path.dirname(logPath), { recursive: true })
    fd = fsImpl.openSync(logPath, 'a')
    fsImpl.writeSync(fd, `\n===== updater launched ${now().toISOString()} =====\n`)
  } catch (err) {
    if (fd !== undefined) {
      try {
        fsImpl.closeSync(fd)
      } catch {
        void 0
      }
    }
    return { stdio: 'ignore', close() {}, logPath: null, error: err?.message || String(err) }
  }
  let closed = false
  return {
    stdio: ['ignore', fd, fd],
    logPath,
    error: null,
    close() {
      if (closed) return
      closed = true
      try {
        fsImpl.closeSync(fd)
      } catch {
        void 0
      }
    }
  }
}

// One readable line for the overlay + desktop.log when the hand-off fails.
// `spawn()` reports ENOENT/EACCES asynchronously through the child's 'error'
// event; name the binary so the user (and the support case) can see *what*
// could not be started rather than a bare "spawn EACCES".
function describeUpdaterLaunchFailure(error, updater) {
  const code = error && typeof error === 'object' && typeof error.code === 'string' ? error.code : ''
  const message = (error && typeof error === 'object' && error.message) || String(error || 'unknown error')
  const target = updater ? ` (${updater})` : ''
  if (code === 'ENOENT') return `Hermes updater not found${target}. Reinstall Hermes-Setup or run \`hermes update\` in a terminal.`
  if (code === 'EACCES' || code === 'EPERM') return `Hermes updater is not executable${target}: ${message}`
  return `Hermes updater could not be started${target}: ${message}`
}

module.exports = {
  UPDATER_LAUNCH_LOG,
  describeUpdaterLaunchFailure,
  openUpdaterLogStdio,
  resolveDetachedCheckoutChannel
}
