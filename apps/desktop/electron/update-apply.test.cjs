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
  resolveDetachedCheckoutChannel,
  describeNoReleaseError,
  createTransientFailureTracker,
  transientNetworkError
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

// AIS-346 / SUP-20260915-125435: a staged updater that is not a native
// executable (the installer's self-update had renamed HermesSetup.dmg onto
// ~/.hermes/hermes-setup) must be recognised before the spawn, not after.
const { inspectUpdaterBinary, isNativeExecutableHead, noStableReleasePublished, quarantineUpdaterBinary } = require('./update-apply.cjs')

function tmpFile(name, bytes) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-updater-'))
  const file = path.join(dir, name)
  fs.writeFileSync(file, Buffer.from(bytes))
  return file
}

test('isNativeExecutableHead: per-platform magic', () => {
  assert.equal(isNativeExecutableHead([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 1], 'darwin'), true)
  assert.equal(isNativeExecutableHead([0xca, 0xfe, 0xba, 0xbe, 0, 0, 0, 2], 'darwin'), true)
  assert.equal(isNativeExecutableHead([0x7f, 0x45, 0x4c, 0x46], 'darwin'), false)
  assert.equal(isNativeExecutableHead([0x7f, 0x45, 0x4c, 0x46, 2, 1, 1, 0], 'linux'), true)
  assert.equal(isNativeExecutableHead(Buffer.from('MZ\x90\x00'), 'win32'), true)
  assert.equal(isNativeExecutableHead(Buffer.from('<!DOCTYPE html>'), 'win32'), false)
  assert.equal(isNativeExecutableHead([], 'darwin'), false)
})

test('inspectUpdaterBinary: accepts a Mach-O, rejects a disk image with a readable reason', () => {
  const macho = tmpFile('hermes-setup', [0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 1, 0, 0, 0, 0])
  assert.deepEqual(inspectUpdaterBinary(macho, { platform: 'darwin' }), { ok: true, expected: 'Mach-O' })

  // A DMG: arbitrary compressed bytes up front, the 512-byte "koly" trailer at the end.
  const dmgBytes = Buffer.alloc(2048, 0x11)
  Buffer.from('koly').copy(dmgBytes, dmgBytes.length - 512)
  const dmg = tmpFile('hermes-setup', dmgBytes)
  const verdict = inspectUpdaterBinary(dmg, { platform: 'darwin' })
  assert.equal(verdict.ok, false)
  assert.equal(verdict.expected, 'Mach-O')
  assert.match(verdict.reason, /macOS disk image/)

  const html = tmpFile('hermes-setup', Buffer.from('<html>404</html>'))
  assert.match(inspectUpdaterBinary(html, { platform: 'darwin' }).reason, /text\/HTML\/JSON document, not Mach-O/)

  const zip = tmpFile('hermes-setup.exe', Buffer.from('PK\x03\x04rest'))
  assert.match(inspectUpdaterBinary(zip, { platform: 'win32' }).reason, /zip archive, not Windows PE/)

  const missing = path.join(path.dirname(macho), 'nope')
  assert.match(inspectUpdaterBinary(missing, { platform: 'linux' }).reason, /^unreadable:/)
})

test('quarantineUpdaterBinary: moves the file aside with a timestamp, null when it cannot', () => {
  const file = tmpFile('hermes-setup', [1, 2, 3])
  const parked = quarantineUpdaterBinary(file, { now: () => new Date('2026-09-15T12:54:07.083Z') })
  assert.equal(parked, `${file}.broken-2026-09-15T12-54-07-083Z`)
  assert.equal(fs.existsSync(file), false)
  assert.equal(fs.existsSync(parked), true)
  assert.equal(quarantineUpdaterBinary(file), null)
})

// AIS-345: only pre-releases in the release repository → 404 on
// releases/latest is "no stable release yet", not an outage.
test('noStableReleasePublished: 404 on the stable channel only', () => {
  const notFound = Object.assign(new Error('HTTP 404 for …/releases/latest/download/hermes-release.json'), { code: 'fetch-failed', status: 404 })
  assert.equal(noStableReleasePublished(notFound, 'stable'), true)
  assert.equal(noStableReleasePublished(notFound, 'tags'), true)
  assert.equal(noStableReleasePublished(notFound, 'preview'), false)
  assert.equal(noStableReleasePublished(notFound, 'main'), false)
  const offline = Object.assign(new Error('getaddrinfo ENOTFOUND github.com'), { code: 'fetch-failed' })
  assert.equal(noStableReleasePublished(offline, 'stable'), false)
  const limited = Object.assign(new Error('rate limit'), { code: 'rate-limited', status: 403 })
  assert.equal(noStableReleasePublished(limited, 'stable'), false)
  assert.equal(noStableReleasePublished(null, 'stable'), false)
  // AIS-350: the preview feed answered but holds no release with a manifest yet.
  const empty = Object.assign(new Error('no preview release with a hermes-release.json asset found'), { code: 'fetch-failed', noRelease: true })
  assert.equal(noStableReleasePublished(empty, 'preview'), true)
  assert.equal(noStableReleasePublished(Object.assign(new Error('x'), { code: 'fetch-failed', noRelease: false }), 'preview'), false)
  // A body error on a 2xx keeps its status and is still an outage.
  const notJson = Object.assign(new Error('response from …/hermes-release.json is not JSON'), { code: 'fetch-failed', status: 200 })
  assert.equal(noStableReleasePublished(notJson, 'stable'), false)
})

test('describeNoReleaseError names the cause for the benign log line', () => {
  assert.equal(describeNoReleaseError(Object.assign(new Error('x'), { status: 404 })), 'HTTP 404')
  assert.equal(describeNoReleaseError(Object.assign(new Error('x'), { noRelease: true })), 'no release with a manifest')
  assert.equal(describeNoReleaseError(new Error('boom')), 'boom')
})

// AIS-414 / SUP-20260924-064314, -055844, SUP-20260923-211245: timeouts and
// network changes on the release feed are the client's connectivity.
test('transientNetworkError: timeouts, network changes and DNS only', () => {
  const timeout = Object.assign(new Error('request to https://api.github.com/repos/x/releases?per_page=30 timed out after 5000 ms'), { code: 'fetch-failed' })
  assert.equal(transientNetworkError(timeout), true)
  assert.equal(transientNetworkError(Object.assign(new Error('net::ERR_NETWORK_CHANGED'), { code: 'fetch-failed' })), true)
  assert.equal(transientNetworkError(Object.assign(new Error('net::ERR_INTERNET_DISCONNECTED'), { code: 'fetch-failed' })), true)
  assert.equal(transientNetworkError(Object.assign(new Error('getaddrinfo ENOTFOUND github.com'), { code: 'fetch-failed' })), true)
  assert.equal(transientNetworkError(Object.assign(new Error('socket hang up'), { transient: true })), true)
  // Real answers from the release repository keep reporting.
  assert.equal(transientNetworkError(Object.assign(new Error('HTTP 502 for …'), { code: 'fetch-failed', status: 502 })), false)
  assert.equal(transientNetworkError(Object.assign(new Error('response from … timed out after x'), { status: 200 })), false)
  assert.equal(transientNetworkError(Object.assign(new Error('rate limit'), { code: 'rate-limited' })), false)
  assert.equal(transientNetworkError(Object.assign(new Error('x'), { noRelease: true })), false)
  assert.equal(transientNetworkError(new Error('invalid release manifest at …: missing tag')), false)
  assert.equal(transientNetworkError(null), false)
})

test('createTransientFailureTracker: reports once per streak after the window, success resets', () => {
  let now = 0
  const tracker = createTransientFailureTracker({ windowMs: 1000, now: () => now })
  assert.equal(tracker.failure('stable'), false)
  now = 999
  assert.equal(tracker.failure('stable'), false)
  now = 1000
  assert.equal(tracker.failure('stable'), true)
  now = 5000
  assert.equal(tracker.failure('stable'), false, 'one report per streak')
  assert.equal(tracker.failure('preview'), false, 'streaks are per key')
  tracker.success('stable')
  now = 6000
  assert.equal(tracker.failure('stable'), false, 'a success starts a fresh streak')
  now = 7000
  assert.equal(tracker.failure('stable'), true)
})
