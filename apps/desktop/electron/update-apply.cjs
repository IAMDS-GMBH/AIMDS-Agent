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

// AIS-346 / SUP-20260915-125435: the staged updater (`~/.hermes/hermes-setup`)
// turned out to be `HermesSetup.dmg` — the installer's self-update had renamed
// the downloaded *disk image* onto the binary. Every hand-off then died with
// `spawn ENOEXEC` and the client had no way to update through the GUI. Before
// the desktop spawns the staged updater it now checks that the file starts
// with this OS's executable magic; anything else is moved aside so the
// macOS/Linux in-app update path (`applyUpdatesPosixInApp`) takes over.
const EXECUTABLE_MAGIC = {
  win32: { expected: 'Windows PE', heads: [[0x4d, 0x5a]] },
  darwin: {
    expected: 'Mach-O',
    heads: [
      [0xcf, 0xfa, 0xed, 0xfe],
      [0xce, 0xfa, 0xed, 0xfe],
      [0xfe, 0xed, 0xfa, 0xcf],
      [0xfe, 0xed, 0xfa, 0xce],
      [0xca, 0xfe, 0xba, 0xbe],
      [0xbe, 0xba, 0xfe, 0xca]
    ]
  },
  linux: { expected: 'ELF', heads: [[0x7f, 0x45, 0x4c, 0x46]] }
}

function executableMagicFor(platform) {
  return EXECUTABLE_MAGIC[platform] || EXECUTABLE_MAGIC.linux
}

function isNativeExecutableHead(head, platform = process.platform) {
  const bytes = Buffer.isBuffer(head) ? head : Buffer.from(head || [])
  return executableMagicFor(platform).heads.some(magic => bytes.length >= magic.length && magic.every((b, i) => bytes[i] === b))
}

// What a rejected file most likely is, for the log line and the support case.
function describeForeignHead(head, tail) {
  const bytes = Buffer.isBuffer(head) ? head : Buffer.from(head || [])
  if (tail && tail.length >= 4 && tail.subarray(0, 4).toString('latin1') === 'koly') return 'a macOS disk image (.dmg)'
  if (bytes.length >= 2 && bytes[0] === 0x50 && bytes[1] === 0x4b) return 'a zip archive'
  if (bytes.length >= 1 && (bytes[0] === 0x3c || bytes[0] === 0x7b)) return 'a text/HTML/JSON document'
  if (bytes.length === 0) return 'an empty file'
  return `an unknown file (magic ${Array.from(bytes.subarray(0, 4)).map(b => b.toString(16).padStart(2, '0')).join(' ')})`
}

// `{ ok: true }` when `file` is a native executable for `platform`, otherwise
// `{ ok: false, expected, reason }`. Read errors count as "not ok" — a file
// the desktop cannot even read will not spawn either.
function inspectUpdaterBinary(file, { fsImpl = fs, platform = process.platform } = {}) {
  const { expected } = executableMagicFor(platform)
  let fd
  try {
    fd = fsImpl.openSync(file, 'r')
    const size = fsImpl.fstatSync(fd).size
    const head = Buffer.alloc(8)
    const n = fsImpl.readSync(fd, head, 0, head.length, 0)
    let tail = null
    if (size >= 512) {
      // A DMG carries its "koly" trailer in the last 512 bytes.
      tail = Buffer.alloc(512)
      fsImpl.readSync(fd, tail, 0, tail.length, size - 512)
    }
    const bytes = head.subarray(0, n)
    if (isNativeExecutableHead(bytes, platform)) return { ok: true, expected }
    return { ok: false, expected, reason: `${describeForeignHead(bytes, tail)}, not ${expected}` }
  } catch (err) {
    return { ok: false, expected, reason: `unreadable: ${err?.message || err}` }
  } finally {
    if (fd !== undefined) {
      try {
        fsImpl.closeSync(fd)
      } catch {
        void 0
      }
    }
  }
}

// Moves a rejected staged updater aside (`hermes-setup.broken-<timestamp>`) so
// `resolveUpdaterBinary()` stops finding it. Returns the new path, or null
// when the rename failed (the caller then just reports the bad file).
function quarantineUpdaterBinary(file, { fsImpl = fs, now = () => new Date() } = {}) {
  const stamp = now().toISOString().replace(/[:.]/g, '-')
  const parked = `${file}.broken-${stamp}`
  try {
    fsImpl.renameSync(file, parked)
    return parked
  } catch {
    return null
  }
}

// AIS-345: `releases/latest/download/…` answers 404 while the release
// repository holds only pre-releases (GitHub's "latest" skips them). For the
// stable channel that is "nothing published yet", not an outage — the update
// check falls back to the source repository's tags exactly as designed
// (AIS-318) and must not file a support case for it every 24 h.
function noStableReleasePublished(error, channel) {
  if (!error || typeof error !== 'object') return false
  // AIS-350: the preview feed answered but no release carries a manifest yet
  // (`fetchReleaseManifest` marks that case) — same class as the stable 404.
  if (error.noRelease === true) return true
  const status = Number(error.status)
  return status === 404 && normalizeChannel(channel || '') === 'stable'
}

// Short cause for the benign "nothing published yet" log line.
function describeNoReleaseError(error) {
  if (error && typeof error === 'object' && error.noRelease === true) return 'no release with a manifest'
  const status = error && typeof error === 'object' ? Number(error.status) : NaN
  return Number.isFinite(status) ? `HTTP ${status}` : String(error?.message || error)
}

// AIS-414: a timeout, a dropped/changed network or a DNS hiccup on the release
// feed is the client's connectivity, not an outage of the release repository.
// Anything that carries an HTTP status (404, 5xx, a bad body on 200) or a rate
// limit is not transient here — those keep reporting.
const TRANSIENT_NETWORK_PATTERN = /timed out after|net::ERR_(NETWORK_|INTERNET_DISCONNECTED|NAME_NOT_RESOLVED|CONNECTION_|TIMED_OUT|ADDRESS_UNREACHABLE|PROXY_CONNECTION_FAILED)|\b(ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|ENETDOWN|EHOSTUNREACH|ECONNREFUSED)\b/

function transientNetworkError(error) {
  if (!error || typeof error !== 'object') return false
  if (error.code === 'rate-limited' || error.noRelease === true) return false
  if (error.status != null && Number.isFinite(Number(error.status))) return false
  if (error.transient === true) return true
  return TRANSIENT_NETWORK_PATTERN.test(String(error.message || ''))
}

// AIS-414: transient failures stay log-only until they persist. The tracker
// remembers when the current streak of failures for a key started; `failure`
// answers true exactly once per streak, when it has lasted `windowMs`.
// `success` ends the streak.
function createTransientFailureTracker({ windowMs = 24 * 60 * 60 * 1000, now = () => Date.now() } = {}) {
  const streaks = new Map()
  return {
    failure(key) {
      const at = now()
      const streak = streaks.get(key)
      if (!streak) {
        streaks.set(key, { since: at, reported: false })
        return false
      }
      if (!streak.reported && at - streak.since >= windowMs) {
        streak.reported = true
        return true
      }
      return false
    },
    success(key) {
      streaks.delete(key)
    }
  }
}

module.exports = {
  TRANSIENT_NETWORK_PATTERN,
  createTransientFailureTracker,
  transientNetworkError,
  UPDATER_LAUNCH_LOG,
  describeUpdaterLaunchFailure,
  inspectUpdaterBinary,
  isNativeExecutableHead,
  describeNoReleaseError,
  noStableReleasePublished,
  openUpdaterLogStdio,
  quarantineUpdaterBinary,
  resolveDetachedCheckoutChannel
}
