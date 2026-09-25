// AIS-428: nightly backend restart — scheduled, caught up after sleep, never mid-turn.
const test = require('node:test')
const assert = require('node:assert/strict')

const { createNightlyRestarter, parseRestartTime, restartDue } = require('./nightly-restart.cjs')

const at = s => new Date(s)

test('parseRestartTime: default, custom, off', () => {
  assert.deepEqual(parseRestartTime(undefined), { hours: 3, minutes: 30 })
  assert.deepEqual(parseRestartTime('04:15'), { hours: 4, minutes: 15 })
  assert.equal(parseRestartTime('off'), null)
  assert.deepEqual(parseRestartTime('25:99'), { hours: 3, minutes: 30 })
})

test('restartDue: once a scheduled moment passed since the backend started', () => {
  const time = { hours: 3, minutes: 30 }
  assert.equal(restartDue(at('2026-09-25T03:29:00'), time, at('2026-09-24T15:00:00')), false)
  assert.equal(restartDue(at('2026-09-25T03:31:00'), time, at('2026-09-24T15:00:00')), true)
  assert.equal(restartDue(at('2026-09-25T09:00:00'), time, at('2026-09-25T03:31:00')), false)
})

function harness({ idle = true, start = '2026-09-24T15:00:00' } = {}) {
  let clock = at(start)
  const timers = []
  const calls = { restart: 0, logs: [] }
  const r = createNightlyRestarter({
    restartTime: '03:30',
    now: () => clock,
    isIdle: async () => (typeof idle === 'function' ? idle() : idle),
    restart: async () => { calls.restart += 1 },
    log: line => calls.logs.push(line),
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length },
    clearTimer: () => {},
    setRepeat: () => 0
  })
  return { r, calls, timers, set: s => { clock = at(s) } }
}

test('scheduled restart happens when idle, once per night', async () => {
  const h = harness()
  h.set('2026-09-25T03:31:00')
  assert.equal(await h.r.attempt('scheduled'), true)
  assert.equal(await h.r.attempt('scheduled'), false)
  assert.equal(h.calls.restart, 1)
})

test('busy backend is retried later, never restarted mid-turn', async () => {
  let busy = true
  const h = harness({ idle: () => !busy })
  h.set('2026-09-25T03:31:00')
  assert.equal(await h.r.attempt('scheduled'), false)
  assert.equal(h.calls.restart, 0)
  assert.equal(h.timers.at(-1).ms, 10 * 60 * 1000)
  busy = false
  await h.timers.at(-1).fn()
  assert.equal(h.calls.restart, 1)
})

test('a wake that slept through the restart time restarts shortly after', async () => {
  const h = harness()
  h.set('2026-09-25T07:38:00')
  h.r.onResume()
  assert.equal(h.timers.at(-1).ms, 30 * 1000)
  await h.timers.at(-1).fn()
  assert.equal(h.calls.restart, 1)
  h.r.onResume()
  assert.equal(h.timers.length, 1, 'no second restart the same night')
})

test('an app relaunch counts as tonight\'s restart', async () => {
  const h = harness()
  h.set('2026-09-25T05:00:00')
  h.r.noteBackendStarted()
  assert.equal(await h.r.attempt('scheduled'), false)
})
