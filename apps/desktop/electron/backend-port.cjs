// Backend port handling for the desktop boot (AIS-344, B1).
//
// After an update hand-off the old backend's listener can outlive the port
// probe: children it spawned (REPL, pty, gateway) inherit the listen fd, so
// `isPortAvailable(port)` says "free", uvicorn's bind fails with EADDRINUSE,
// and the backend exits before it is ready. The AIS-276 retry re-picked the
// same port because the probe kept saying "free" — 31 failed boots in one
// desktop.log, "hangs after restart" for users.
//
// Three pieces, all injectable so they run under node:test without sockets:
//   * pickPort(exclude)        — skips ports that already failed this boot
//   * waitForPortRelease(...)  — before the first spawn, wait until the port
//                                the previous backend used is free (≤10 s);
//                                SIGTERM a stale backend PID if it is alive
//   * stopProcessAndWait(...)  — hand-off: stop our backend and wait for exit
//                                (≤5 s) before app.quit(), on every platform
// plus the record file `~/.hermes/desktop-backend.json` = {port, pid, started_at}
// that carries the last backend identity across the restart.

const fs = require('node:fs')
const path = require('node:path')

const BACKEND_RECORD_FILE = 'desktop-backend.json'
const SPAWN_ATTEMPTS = 5
const SPAWN_BACKOFF_MS = [2000, 3000, 4000, 5000]
const PORT_RELEASE_TIMEOUT_MS = 10000
const STOP_TIMEOUT_MS = 5000

const PORT_RACE_RE = /address already in use|EADDRINUSE|Errno 10048|Errno 48|Errno 98/i

function isPortRaceText(text) {
  return PORT_RACE_RE.test(String(text || ''))
}

/** Delay before spawn attempt `attempt + 1` (attempt is 1-based, failed). */
function spawnBackoffMs(attempt) {
  const index = Math.max(0, Math.min(SPAWN_BACKOFF_MS.length - 1, attempt - 1))
  return SPAWN_BACKOFF_MS[index]
}

/**
 * First free port in [floor, ceiling] that is not in `exclude`.
 * `exclude` collects the ports that failed with EADDRINUSE in this boot, so a
 * retry never re-picks a port the probe wrongly reports as free.
 */
async function pickPort({ isPortAvailable, floor, ceiling, exclude = new Set() }) {
  for (let port = floor; port <= ceiling; port += 1) {
    if (exclude.has(port)) continue
    if (await isPortAvailable(port)) return port
  }
  throw new Error(`No free localhost port in ${floor}-${ceiling}` + (exclude.size ? ` (skipped ${[...exclude].join(', ')})` : ''))
}

function backendRecordPath(hermesHome) {
  return path.join(hermesHome, BACKEND_RECORD_FILE)
}

function readBackendRecord(hermesHome) {
  try {
    const raw = fs.readFileSync(backendRecordPath(hermesHome), 'utf8')
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const port = Number(parsed.port)
    const pid = Number(parsed.pid)
    if (!Number.isInteger(port) || port <= 0) return null
    return {
      port,
      pid: Number.isInteger(pid) && pid > 0 ? pid : null,
      started_at: typeof parsed.started_at === 'string' ? parsed.started_at : null
    }
  } catch {
    return null
  }
}

function writeBackendRecord(hermesHome, { port, pid, startedAt = new Date() }) {
  try {
    fs.mkdirSync(hermesHome, { recursive: true })
    const record = {
      port,
      pid: Number.isInteger(pid) ? pid : null,
      started_at: startedAt instanceof Date ? startedAt.toISOString() : String(startedAt)
    }
    fs.writeFileSync(backendRecordPath(hermesHome), JSON.stringify(record, null, 2) + '\n')
    return record
  } catch {
    return null
  }
}

function clearBackendRecord(hermesHome) {
  try {
    fs.unlinkSync(backendRecordPath(hermesHome))
  } catch {
    void 0
  }
}

function defaultIsPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    // EPERM: the process exists but belongs to someone else — treat as alive.
    return error && error.code === 'EPERM'
  }
}

function defaultTerminate(pid) {
  try {
    process.kill(pid, 'SIGTERM')
    return true
  } catch {
    return false
  }
}

/**
 * Wait until the port the previous backend used is really free.
 *
 * Returns { waited_ms, released, terminated_pid, skipped } — `skipped` when
 * there was nothing to wait for (no record, or the record names our own
 * process). A stale backend PID that is still alive after `graceMs` gets a
 * SIGTERM; we keep polling until the port frees or the timeout elapses.
 */
async function waitForPortRelease({
  record,
  isPortAvailable,
  isPidAlive = defaultIsPidAlive,
  terminate = defaultTerminate,
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  now = () => Date.now(),
  timeoutMs = PORT_RELEASE_TIMEOUT_MS,
  graceMs = 2000,
  pollMs = 250,
  ownPid = process.pid,
  ownBackendPid = null,
  log = () => {}
}) {
  if (!record || !record.port) return { skipped: true, released: true, waited_ms: 0, terminated_pid: null }
  if (record.pid && record.pid === ownPid) return { skipped: true, released: true, waited_ms: 0, terminated_pid: null }
  // AIS-352: the record may name OUR OWN live backend (a boot chain that is
  // still starting it). Never SIGTERM that one — the caller waits for its
  // readiness instead. Only a backend nobody in this process owns is stale.
  if (record.pid && ownBackendPid && record.pid === ownBackendPid) {
    log(`[boot] port ${record.port} belongs to our own starting backend (pid ${record.pid}) — not touching it`)
    return { skipped: true, released: true, waited_ms: 0, terminated_pid: null, own_backend: true }
  }

  const started = now()
  let terminatedPid = null
  let polls = 0
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const free = await isPortAvailable(record.port)
    const stalePidAlive = record.pid ? isPidAlive(record.pid) : false
    if (free && !stalePidAlive) {
      return { skipped: false, released: true, waited_ms: now() - started, terminated_pid: terminatedPid }
    }
    const elapsed = now() - started
    if (stalePidAlive && terminatedPid === null && elapsed >= graceMs) {
      log(`[boot] previous backend (pid ${record.pid}) still alive on port ${record.port} after ${elapsed}ms — sending SIGTERM`)
      terminatedPid = terminate(record.pid) ? record.pid : -1
    }
    if (elapsed >= timeoutMs) {
      log(`[boot] port ${record.port} not released after ${elapsed}ms (previous backend pid ${record.pid || '?'}) — picking another port`)
      return { skipped: false, released: false, waited_ms: elapsed, terminated_pid: terminatedPid }
    }
    polls += 1
    if (polls === 1) {
      log(`[boot] waiting for previous backend port ${record.port} to be released (pid ${record.pid || '?'})`)
    }
    await sleep(pollMs)
  }
}

/**
 * SIGTERM `proc` and resolve once it exited, or after `timeoutMs`
 * (then SIGKILL as a last resort). Resolves { exited, forced, waited_ms }.
 */
function stopProcessAndWait(proc, { timeoutMs = STOP_TIMEOUT_MS, now = () => Date.now(), log = () => {} } = {}) {
  return new Promise(resolve => {
    if (!proc || typeof proc.once !== 'function' || proc.exitCode !== null && proc.exitCode !== undefined) {
      resolve({ exited: true, forced: false, waited_ms: 0 })
      return
    }
    const started = now()
    let settled = false
    const finish = (exited, forced) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ exited, forced, waited_ms: now() - started })
    }
    proc.once('exit', () => finish(true, false))
    const timer = setTimeout(() => {
      log(`[boot] backend did not exit within ${timeoutMs}ms after SIGTERM — SIGKILL`)
      try {
        proc.kill('SIGKILL')
      } catch {
        void 0
      }
      finish(false, true)
    }, timeoutMs)
    try {
      if (!proc.killed) proc.kill('SIGTERM')
    } catch {
      finish(true, false)
    }
  })
}

module.exports = {
  BACKEND_RECORD_FILE,
  PORT_RELEASE_TIMEOUT_MS,
  SPAWN_ATTEMPTS,
  SPAWN_BACKOFF_MS,
  STOP_TIMEOUT_MS,
  backendRecordPath,
  clearBackendRecord,
  isPortRaceText,
  pickPort,
  readBackendRecord,
  spawnBackoffMs,
  stopProcessAndWait,
  waitForPortRelease,
  writeBackendRecord
}
