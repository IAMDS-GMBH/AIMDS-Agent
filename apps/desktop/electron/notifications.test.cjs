const assert = require('node:assert/strict')
const test = require('node:test')

const {
  badgeOverlaySvgDataUrl,
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

test('badgeOverlaySvgDataUrl renders the count and caps at 99+', () => {
  const decode = url => Buffer.from(url.replace(/^data:image\/svg\+xml;base64,/, ''), 'base64').toString('utf8')

  assert.match(decode(badgeOverlaySvgDataUrl(3)), />3<\/text>/)
  assert.match(decode(badgeOverlaySvgDataUrl(150)), />99\+<\/text>/)
  assert.match(badgeOverlaySvgDataUrl(1), /^data:image\/svg\+xml;base64,/)
})
