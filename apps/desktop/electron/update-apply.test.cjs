// AIS-331 / SUP-20260914-105316: the update hand-off must never fail silently.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const {
  UPDATER_LAUNCH_LOG,
  describeUpdaterLaunchFailure,
  openUpdaterLogStdio,
  resolveDetachedCheckoutChannel
} = require('./update-apply.cjs')

test('resolveDetachedCheckoutChannel: detached HEAD git checkout on main → stable', () => {
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'main', marker: null, headRef: 'HEAD' }),
    { branch: 'stable', coerced: true, from: 'main' }
  )
  // A feature branch configured on a detached checkout is just as wrong.
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'bb/gui', marker: null, headRef: 'HEAD\n' }),
    { branch: 'stable', coerced: true, from: 'bb/gui' }
  )
})

test('resolveDetachedCheckoutChannel: leaves branch checkouts, tag channels and release installs alone', () => {
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'main', marker: null, headRef: 'main' }),
    { branch: 'main', coerced: false }
  )
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'preview', marker: null, headRef: 'HEAD' }),
    { branch: 'preview', coerced: false }
  )
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'tags', marker: null, headRef: 'HEAD' }),
    { branch: 'stable', coerced: false }
  )
  // Release-managed installs are coerced elsewhere (effectiveUpdateChannel).
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'main', marker: { version: '0.7.5' }, headRef: 'HEAD' }),
    { branch: 'main', coerced: false }
  )
  // Unknown head ref (git failed) is not "detached".
  assert.deepEqual(
    resolveDetachedCheckoutChannel({ branch: 'main', marker: null, headRef: '' }),
    { branch: 'main', coerced: false }
  )
})

test('openUpdaterLogStdio appends a header and hands out an fd for stdout+stderr', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-updater-log-'))
  try {
    const logPath = path.join(dir, 'logs', UPDATER_LAUNCH_LOG)
    const now = () => new Date('2026-09-14T10:53:00.000Z')
    const first = openUpdaterLogStdio(logPath, { now })
    assert.equal(first.error, null)
    assert.equal(first.logPath, logPath)
    assert.equal(first.stdio[0], 'ignore')
    assert.equal(typeof first.stdio[1], 'number')
    assert.equal(first.stdio[1], first.stdio[2])
    fs.writeSync(first.stdio[1], 'hermes update: already up to date\n')
    first.close()
    first.close() // idempotent

    const second = openUpdaterLogStdio(logPath, { now })
    second.close()

    const text = fs.readFileSync(logPath, 'utf8')
    assert.equal((text.match(/===== updater launched 2026-09-14T10:53:00.000Z =====/g) || []).length, 2)
    assert.match(text, /already up to date/)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('openUpdaterLogStdio falls back to ignore when the log cannot be opened', () => {
  const fsImpl = {
    mkdirSync() {},
    openSync() {
      const err = new Error('EACCES: permission denied')
      err.code = 'EACCES'
      throw err
    },
    writeSync() {},
    closeSync() {}
  }
  const out = openUpdaterLogStdio('/nowhere/updater-launch.log', { fsImpl })
  assert.equal(out.stdio, 'ignore')
  assert.equal(out.logPath, null)
  assert.match(out.error, /EACCES/)
  out.close()
})

test('describeUpdaterLaunchFailure names the binary and the cause', () => {
  const enoent = Object.assign(new Error('spawn /Users/x/.hermes/hermes-setup ENOENT'), { code: 'ENOENT' })
  assert.match(describeUpdaterLaunchFailure(enoent, '/Users/x/.hermes/hermes-setup'), /not found \(\/Users\/x\/.hermes\/hermes-setup\)/)
  const eacces = Object.assign(new Error('spawn EACCES'), { code: 'EACCES' })
  assert.match(describeUpdaterLaunchFailure(eacces, 'hermes-setup'), /not executable \(hermes-setup\): spawn EACCES/)
  assert.match(describeUpdaterLaunchFailure(new Error('boom'), ''), /could not be started: boom/)
  assert.match(describeUpdaterLaunchFailure(null, ''), /unknown error/)
})
