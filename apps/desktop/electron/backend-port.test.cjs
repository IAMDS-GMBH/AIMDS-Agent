// AIS-344 (B1): the restart port race — a retry must never re-pick a port that
// just failed with EADDRINUSE, the first boot after a hand-off waits for the
// previous backend's port, and the hand-off stops the backend before quitting.
const test = require('node:test')
const assert = require('node:assert/strict')
const EventEmitter = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const {
  BACKEND_RECORD_FILE,
  SPAWN_ATTEMPTS,
  clearBackendRecord,
  isPortRaceText,
  pickPort,
  readBackendRecord,
  spawnBackoffMs,
  stopProcessAndWait,
  waitForPortRelease,
  writeBackendRecord
} = require('./backend-port.cjs')

function mkHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-backend-port-'))
}

test('pickPort skips ports that failed this boot even when the probe says free', async () => {
  const probed = []
  const isPortAvailable = async port => {
    probed.push(port)
    return true // the probe lies after a hand-off (children hold the listen fd)
  }
  assert.equal(await pickPort({ isPortAvailable, floor: 9120, ceiling: 9125 }), 9120)
  assert.equal(await pickPort({ isPortAvailable, floor: 9120, ceiling: 9125, exclude: new Set([9120]) }), 9121)
  assert.equal(await pickPort({ isPortAvailable, floor: 9120, ceiling: 9125, exclude: new Set([9120, 9121]) }), 9122)
  assert.ok(!probed.includes(9120) || probed.indexOf(9120) === 0, 'excluded ports are not even probed')
})

test('pickPort reports the skipped ports when the range is exhausted', async () => {
  await assert.rejects(
    pickPort({ isPortAvailable: async () => false, floor: 9120, ceiling: 9121, exclude: new Set([9120]) }),
    /No free localhost port in 9120-9121 \(skipped 9120\)/
  )
})

test('spawn attempts and backoff: 5 attempts, 2/3/4/5 s', () => {
  assert.equal(SPAWN_ATTEMPTS, 5)
  assert.deepEqual([1, 2, 3, 4, 9].map(spawnBackoffMs), [2000, 3000, 4000, 5000, 5000])
})

test('isPortRaceText recognises the bind failures of every platform', () => {
  assert.ok(isPortRaceText('[Errno 48] address already in use'))
  assert.ok(isPortRaceText('OSError: [Errno 98] Address already in use'))
  assert.ok(isPortRaceText('[WinError 10048] Only one usage of each socket address\nERROR: [Errno 10048] error while attempting to bind'))
  assert.ok(isPortRaceText('Error: listen EADDRINUSE: address already in use 127.0.0.1:9120'))
  assert.ok(!isPortRaceText('Hermes backend exited before it became ready (1).'))
})

test('backend record round-trips through ~/.hermes/desktop-backend.json', () => {
  const home = mkHome()
  assert.equal(readBackendRecord(home), null)
  const written = writeBackendRecord(home, { port: 9120, pid: 4242, startedAt: new Date('2026-09-15T11:13:00Z') })
  assert.deepEqual(written, { port: 9120, pid: 4242, started_at: '2026-09-15T11:13:00.000Z' })
  assert.deepEqual(readBackendRecord(home), { port: 9120, pid: 4242, started_at: '2026-09-15T11:13:00.000Z' })
  assert.ok(fs.existsSync(path.join(home, BACKEND_RECORD_FILE)))
  fs.writeFileSync(path.join(home, BACKEND_RECORD_FILE), '{not json')
  assert.equal(readBackendRecord(home), null, 'a corrupt record is ignored')
  clearBackendRecord(home)
  assert.ok(!fs.existsSync(path.join(home, BACKEND_RECORD_FILE)))
  clearBackendRecord(home) // idempotent
})

function fakeClock() {
  let t = 0
  return {
    now: () => t,
    sleep: async ms => {
      t += ms
    }
  }
}

test('waitForPortRelease returns immediately when the previous port is free and the pid is gone', async () => {
  const clock = fakeClock()
  const outcome = await waitForPortRelease({
    record: { port: 9120, pid: 4242 },
    isPortAvailable: async () => true,
    isPidAlive: () => false,
    terminate: () => assert.fail('nothing to terminate'),
    ...clock
  })
  assert.deepEqual(outcome, { skipped: false, released: true, waited_ms: 0, terminated_pid: null })
})

test('waitForPortRelease polls until the port frees and SIGTERMs a stale backend after the grace period', async () => {
  const clock = fakeClock()
  let alive = true
  let freeAt = 3000
  const terminated = []
  const logs = []
  const outcome = await waitForPortRelease({
    record: { port: 9120, pid: 4242 },
    isPortAvailable: async () => clock.now() >= freeAt,
    isPidAlive: () => alive,
    terminate: pid => {
      terminated.push(pid)
      alive = false
      freeAt = clock.now() + 250
      return true
    },
    graceMs: 2000,
    pollMs: 250,
    timeoutMs: 10000,
    log: line => logs.push(line),
    ...clock
  })
  assert.deepEqual(terminated, [4242], 'stale pid terminated once')
  assert.equal(outcome.released, true)
  assert.equal(outcome.terminated_pid, 4242)
  assert.ok(outcome.waited_ms >= 2000 && outcome.waited_ms <= 3000, `waited ${outcome.waited_ms}`)
  assert.ok(logs.some(l => /SIGTERM/.test(l)))
})

test('waitForPortRelease gives up after the timeout so the boot picks another port', async () => {
  const clock = fakeClock()
  const outcome = await waitForPortRelease({
    record: { port: 9120, pid: null },
    isPortAvailable: async () => false,
    isPidAlive: () => false,
    timeoutMs: 10000,
    pollMs: 500,
    ...clock
  })
  assert.equal(outcome.released, false)
  assert.equal(outcome.waited_ms, 10000)
})

test('waitForPortRelease skips a record without port or naming our own process', async () => {
  const skipped = await waitForPortRelease({ record: null, isPortAvailable: async () => false })
  assert.equal(skipped.skipped, true)
  const own = await waitForPortRelease({ record: { port: 9120, pid: 77 }, ownPid: 77, isPortAvailable: async () => false })
  assert.equal(own.skipped, true)
})

// AIS-352: the 2026-09-16 boot storm — the retry found the record of the
// backend THIS process had just spawned (still blocked on a macOS dialog) and
// SIGTERMed it after 2 s. Our own live backend is never a stale one.
test('waitForPortRelease never terminates our own live backend', async () => {
  const signals = []
  const logs = []
  const own = await waitForPortRelease({
    record: { port: 9120, pid: 4242 },
    ownPid: 1,
    ownBackendPid: 4242,
    isPortAvailable: async () => false,
    isPidAlive: () => true,
    terminate: pid => (signals.push(pid), true),
    log: line => logs.push(line)
  })
  assert.equal(own.skipped, true)
  assert.equal(own.own_backend, true)
  assert.deepEqual(signals, [])
  assert.ok(logs.some(l => l.includes('our own starting backend')))
  // A different pid on the same port is still treated as stale.
  const stale = await waitForPortRelease({
    record: { port: 9120, pid: 4243 },
    ownPid: 1,
    ownBackendPid: 4242,
    isPortAvailable: async () => true,
    isPidAlive: () => false
  })
  assert.equal(stale.skipped, false)
  assert.equal(stale.released, true)
})

function fakeProcess({ exitOnTerm = true, delayMs = 0 } = {}) {
  const proc = new EventEmitter()
  proc.exitCode = null
  proc.killed = false
  proc.signals = []
  proc.kill = signal => {
    proc.signals.push(signal)
    proc.killed = true
    if (signal === 'SIGTERM' && exitOnTerm) {
      setTimeout(() => {
        proc.exitCode = 0
        proc.emit('exit', 0, null)
      }, delayMs)
    }
    return true
  }
  return proc
}

test('stopProcessAndWait resolves once the backend exited after SIGTERM', async () => {
  const proc = fakeProcess({ delayMs: 5 })
  const result = await stopProcessAndWait(proc, { timeoutMs: 1000 })
  assert.deepEqual(proc.signals, ['SIGTERM'])
  assert.equal(result.exited, true)
  assert.equal(result.forced, false)
})

test('stopProcessAndWait SIGKILLs after the timeout and reports forced', async () => {
  const proc = fakeProcess({ exitOnTerm: false })
  const logs = []
  const result = await stopProcessAndWait(proc, { timeoutMs: 20, log: l => logs.push(l) })
  assert.deepEqual(proc.signals, ['SIGTERM', 'SIGKILL'])
  assert.equal(result.exited, false)
  assert.equal(result.forced, true)
  assert.ok(logs.some(l => /SIGKILL/.test(l)))
})

test('stopProcessAndWait is a no-op for a missing or already exited process', async () => {
  assert.deepEqual(await stopProcessAndWait(null), { exited: true, forced: false, waited_ms: 0 })
  const proc = fakeProcess()
  proc.exitCode = 1
  assert.deepEqual(await stopProcessAndWait(proc), { exited: true, forced: false, waited_ms: 0 })
  assert.deepEqual(proc.signals, [])
})
