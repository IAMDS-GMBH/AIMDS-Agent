const assert = require('node:assert/strict')
const test = require('node:test')

const zlib = require('node:zlib')

const {
  BADGE_SIZE,
  badgeLabel,
  badgeOverlayPng,
  buildNotificationOptions,
  normalizeBadgeCount,
  normalizeNotificationAction,
  shouldFlashFrame
} = require('./notifications.cjs')

test('buildNotificationOptions falls back to the product title and an empty body', () => {
  const { action, options } = buildNotificationOptions(undefined)

  assert.deepEqual(options, { title: 'Hermes', body: '', silent: false })
  assert.equal(action, null)
})

test('buildNotificationOptions keeps title/body/silent and a valid cron-artifact action', () => {
  const { action, options } = buildNotificationOptions({
    title: '  Cron job completed: Morning brief ',
    body: 'Click to open the report.',
    silent: true,
    action: { kind: 'cron-artifact', jobId: 'job-1', path: '/tmp/brief.md', sessionId: 'cron_job-1_1', profile: 'work' }
  })

  assert.deepEqual(options, {
    title: 'Cron job completed: Morning brief',
    body: 'Click to open the report.',
    silent: true
  })
  assert.deepEqual(action, {
    kind: 'cron-artifact',
    jobId: 'job-1',
    path: '/tmp/brief.md',
    sessionId: 'cron_job-1_1',
    profile: 'work'
  })
})

test('normalizeNotificationAction drops unknown kinds, missing job ids and stray fields', () => {
  assert.equal(normalizeNotificationAction({ kind: 'open-url', url: 'https://x' }), null)
  assert.equal(normalizeNotificationAction({ kind: 'cron-artifact' }), null)
  assert.equal(normalizeNotificationAction('cron-artifact'), null)
  assert.deepEqual(normalizeNotificationAction({ kind: 'cron-artifact', jobId: 'j', extra: 1, path: '' }), {
    kind: 'cron-artifact',
    jobId: 'j'
  })
})

test('normalizeBadgeCount clamps to a non-negative integer', () => {
  assert.equal(normalizeBadgeCount(undefined), 0)
  assert.equal(normalizeBadgeCount(-3), 0)
  assert.equal(normalizeBadgeCount('2.7'), 2)
  assert.equal(normalizeBadgeCount(Number.NaN), 0)
  assert.equal(normalizeBadgeCount(100000), 9999)
})

test('shouldFlashFrame only fires on the 0→N transition while unfocused', () => {
  assert.equal(shouldFlashFrame(0, 1, false), true)
  assert.equal(shouldFlashFrame(0, 1, true), false)
  assert.equal(shouldFlashFrame(1, 2, false), false)
  assert.equal(shouldFlashFrame(2, 0, false), false)
})

test('buildNotificationOptions adds the app icon only when given one (Linux/Windows)', () => {
  assert.equal(buildNotificationOptions({ title: 'x' }).options.icon, undefined)
  assert.equal(
    buildNotificationOptions({ title: 'x' }, { icon: '/opt/hermes/icon.png' }).options.icon,
    '/opt/hermes/icon.png'
  )
})

// Minimal PNG reader for the badge: IHDR + inflated RGBA rows.
function decodePng(buffer) {
  assert.deepEqual([...buffer.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  let offset = 8
  let width = 0
  let height = 0
  const idat = []
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const data = buffer.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      assert.equal(data[8], 8, 'bit depth')
      assert.equal(data[9], 6, 'RGBA')
    }
    if (type === 'IDAT') idat.push(data)
    offset += 12 + length
  }
  const raw = zlib.inflateSync(Buffer.concat(idat))
  const pixel = (x, y) => {
    const start = y * (width * 4 + 1) + 1 + x * 4
    return [...raw.subarray(start, start + 4)]
  }
  return { height, pixel, width }
}

test('badgeLabel caps at 99+', () => {
  assert.equal(badgeLabel(3), '3')
  assert.equal(badgeLabel(99), '99')
  assert.equal(badgeLabel(150), '99+')
})

test('badgeOverlayPng is a real PNG (nativeImage cannot decode SVG, AIS-798)', () => {
  const { height, pixel, width } = decodePng(badgeOverlayPng(3))

  assert.equal(width, BADGE_SIZE)
  assert.equal(height, BADGE_SIZE)
  // Transparent corner, opaque red disc, white count in the middle.
  assert.equal(pixel(0, 0)[3], 0)
  assert.deepEqual(pixel(3, BADGE_SIZE / 2), [0xd9, 0x2d, 0x20, 255])
  let ink = 0
  for (let y = 0; y < BADGE_SIZE; y += 1) {
    for (let x = 0; x < BADGE_SIZE; x += 1) {
      if (pixel(x, y).join() === '255,255,255,255') ink += 1
    }
  }
  assert.ok(ink > 20, `count glyph drawn (${ink} px)`)
})

test('badgeOverlayPng draws a different glyph per count, all inside the disc', () => {
  const images = [1, 42, 150].map(count => badgeOverlayPng(count).toString('base64'))

  assert.equal(new Set(images).size, 3)
  for (const count of [8, 88, 999]) {
    const { pixel } = decodePng(badgeOverlayPng(count))
    // No ink on the transparent rim: every white pixel sits on the disc.
    for (let y = 0; y < BADGE_SIZE; y += 1) {
      for (let x = 0; x < BADGE_SIZE; x += 1) {
        const dx = x + 0.5 - BADGE_SIZE / 2
        const dy = y + 0.5 - BADGE_SIZE / 2
        if (pixel(x, y).join() === '255,255,255,255') {
          assert.ok(dx * dx + dy * dy < (BADGE_SIZE / 2) ** 2, `count ${count}: ink at ${x},${y} off the disc`)
        }
      }
    }
  }
})
