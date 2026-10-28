// AIS-352: readiness wait that reports progress, and the rule that only the
// current child may clear the boot chain's state.
const test = require('node:test')
const assert = require('node:assert/strict')

const { EventEmitter } = require('node:events')

const {
  LOCAL_READY_TIMEOUT_MS,
  REMOTE_READY_TIMEOUT_MS,
  SCHEDULED_RESTART_QUIET_MS,
  createBackendRegistry,
  isScheduledRestartRecovery,
  shouldClearConnectionState,
  shouldDropSingleFlight,
  slowBootMessage,
  waitForBackendReady
} = require('./boot-guard.cjs')

test('deadlines: local start gets minutes, a remote gateway stays short', () => {
  assert.equal(LOCAL_READY_TIMEOUT_MS, 180_000)
  assert.equal(REMOTE_READY_TIMEOUT_MS, 45_000)
})

test('shouldClearConnectionState: only the current child (or no child) may clear', () => {
  const a = { pid: 1 }
  const b = { pid: 2 }
  assert.equal(shouldClearConnectionState({ exitingChild: a, currentChild: a }), true)
  assert.equal(shouldClearConnectionState({ exitingChild: a, currentChild: null }), true)
  // A superseded child exiting late must not touch the live one.
  assert.equal(shouldClearConnectionState({ exitingChild: a, currentChild: b }), false)
})

test('shouldDropSingleFlight: only the chain that started the backend loses its promise', () => {
  const chainA = Promise.resolve('a')
  const chainB = Promise.resolve('b')
  assert.equal(shouldDropSingleFlight({ backendReady: true, ownConnection: chainA, currentConnection: chainA }), true)
  // Nightly restart (AIS-430): teardown cleared the handle, the renderer
  // started chain B, then chain A's old backend exited — B must survive.
  assert.equal(shouldDropSingleFlight({ backendReady: true, ownConnection: chainA, currentConnection: chainB }), false)
  assert.equal(shouldDropSingleFlight({ backendReady: true, ownConnection: chainA, currentConnection: null }), false)
  // While booting, the chain's own .catch clears it, not the exit handler.
  assert.equal(shouldDropSingleFlight({ backendReady: false, ownConnection: chainA, currentConnection: chainA }), false)
})

function fakeChild(pid) {
  const child = new EventEmitter()
  child.pid = pid
  child.killed = false
  child.exitCode = null
  child.signalCode = null
  child.exit = (code = 0) => {
    child.exitCode = code
    child.emit('exit', code, null)
  }
  return child
}

test('backend registry: backends of a superseded chain are strays, exited ones drop out', () => {
  const registry = createBackendRegistry()
  const chainA = {}
  const chainB = {}
  const a = fakeChild(1)
  const b = fakeChild(2)
  const done = fakeChild(3)
  registry.add(a, chainA)
  registry.add(b, chainB)
  registry.add(done, chainA)
  done.exit(0)

  assert.equal(registry.size(), 2)
  assert.deepEqual(registry.strays({ currentChain: chainB, keep: b }), [a])
  // Teardown: nothing is current, everything but the dying backend goes.
  assert.deepEqual(registry.strays({ keep: a }), [b])
  a.killed = true
  assert.deepEqual(registry.strays({ currentChain: chainB }), [])
})

test('isScheduledRestartRecovery: quiet only shortly after a scheduled restart', () => {
  const at = 1_000_000
  assert.equal(isScheduledRestartRecovery({ now: at + 5_000, scheduledRestartAt: at }), true)
  assert.equal(isScheduledRestartRecovery({ now: at + SCHEDULED_RESTART_QUIET_MS + 1, scheduledRestartAt: at }), false)
  assert.equal(isScheduledRestartRecovery({ now: at, scheduledRestartAt: null }), false)
  assert.equal(isScheduledRestartRecovery({ now: at - 1, scheduledRestartAt: at }), false)
})

function clock() {
  let t = 0
  return {
    now: () => t,
    sleep: async ms => {
      t += ms
    }
  }
}

test('waitForBackendReady resolves as soon as the probe answers and reports the wait', async () => {
  const { now, sleep } = clock()
  let calls = 0
  const probe = async () => {
    calls += 1
    if (calls < 4) throw new Error('connect ECONNREFUSED 127.0.0.1:9120')
  }
  const result = await waitForBackendReady({ probe, now, sleep, pollMs: 500 })
  assert.equal(calls, 4)
  assert.equal(result.waited_ms, 1500)
})

test('waitForBackendReady calls onSlow every interval while the backend is still starting', async () => {
  const { now, sleep } = clock()
  let calls = 0
  const slow = []
  const probe = async () => {
    calls += 1
    if (calls < 50) throw new Error('connect ECONNREFUSED')
  }
  // 49 failures × 500 ms = 24.5 s → hints at 10 s and 20 s.
  await waitForBackendReady({ probe, now, sleep, pollMs: 500, slowIntervalMs: 10_000, onSlow: ms => slow.push(ms) })
  assert.deepEqual(slow, [10_000, 20_000])
})

test('waitForBackendReady gives up after the deadline with the last probe error in the message', async () => {
  const { now, sleep } = clock()
  const probe = async () => {
    throw new Error('connect ECONNREFUSED 127.0.0.1:9120')
  }
  await assert.rejects(
    waitForBackendReady({ probe, now, sleep, pollMs: 500, timeoutMs: 3_000 }),
    err => err.code === 'backend-not-ready' && /did not become ready: connect ECONNREFUSED/.test(err.message) && err.waited_ms >= 3_000
  )
})

// AIS-749: the nightly restart spawned the backend in a dark wake, the Mac
// slept again for 17 min, and the wall-clock deadline then stopped a backend
// that was just mounting its routes.
test('waitForBackendReady does not count time asleep against the deadline', async () => {
  let t = 1_000_000
  let sleeps = 0
  const suspended = []
  const slow = []
  const now = () => t
  const sleep = async ms => {
    sleeps += 1
    // The 4th poll sleeps through a 17-minute suspension.
    t += sleeps === 4 ? 17 * 60_000 : ms
  }
  let calls = 0
  const probe = async () => {
    calls += 1
    if (calls < 8) throw new Error('connect ECONNREFUSED 127.0.0.1:9120')
  }
  const result = await waitForBackendReady({
    probe,
    now,
    sleep,
    pollMs: 500,
    timeoutMs: 180_000,
    slowIntervalMs: 10_000,
    onSlow: ms => slow.push(ms),
    onSuspend: ms => suspended.push(ms)
  })
  assert.equal(calls, 8, 'the probe runs again after the wake and succeeds')
  assert.deepEqual(suspended, [17 * 60_000 - 500])
  assert.equal(result.suspended_ms, 17 * 60_000 - 500)
  assert.equal(result.waited_ms, 7 * 500, 'only awake time counts as waiting')
  assert.deepEqual(slow, [], 'no "still waiting" hint for time asleep')
})

test('waitForBackendReady still gives up after the awake deadline once suspended', async () => {
  let t = 0
  let sleeps = 0
  const now = () => t
  const sleep = async ms => {
    sleeps += 1
    t += sleeps === 2 ? 60 * 60_000 : ms
  }
  const probe = async () => {
    throw new Error('connect ECONNREFUSED 127.0.0.1:9120')
  }
  await assert.rejects(
    waitForBackendReady({ probe, now, sleep, pollMs: 500, timeoutMs: 3_000 }),
    err => err.code === 'backend-not-ready' && err.waited_ms >= 3_000 && err.waited_ms < 4_000 && err.suspended_ms === 60 * 60_000 - 500
  )
})

test('waitForBackendReady treats a slow probe as awake time, not as a suspension', async () => {
  let t = 0
  const now = () => t
  const sleep = async ms => {
    t += ms
  }
  const suspended = []
  // Each probe hangs for its full 15 s request timeout.
  const probe = async () => {
    t += 15_000
    throw new Error('timeout')
  }
  await assert.rejects(
    waitForBackendReady({ probe, now, sleep, pollMs: 500, timeoutMs: 60_000, onSuspend: ms => suspended.push(ms) }),
    err => err.code === 'backend-not-ready'
  )
  assert.deepEqual(suspended, [])
})

test('slowBootMessage names the elapsed seconds', () => {
  assert.match(slowBootMessage(12_400), /\(12 s\)/)
  assert.match(slowBootMessage(100), /\(1 s\)/)
})
