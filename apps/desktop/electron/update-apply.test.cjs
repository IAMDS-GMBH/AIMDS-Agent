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
})
