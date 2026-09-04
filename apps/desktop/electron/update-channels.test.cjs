// Release channels (AIS-292): stable follows vX.Y.Z, preview also the
// vX.Y.Z-rc.N candidates; selection is semver, not ls-remote order.
const test = require('node:test')
const assert = require('node:assert/strict')

const {
  compareReleaseTags,
  isTagChannel,
  normalizeChannel,
  parseLsRemoteTags,
  parseReleaseTag,
  selectReleaseTag,
  versionFromTag
} = require('./update-channels.cjs')

test('normalizeChannel maps aliases and keeps branch names', () => {
  assert.equal(normalizeChannel(''), 'main')
  assert.equal(normalizeChannel('tags'), 'stable')
  assert.equal(normalizeChannel('Preview'), 'preview')
  assert.equal(normalizeChannel('bb/gui'), 'bb/gui')
  assert.equal(isTagChannel('tags'), true)
  assert.equal(isTagChannel('main'), false)
})

test('parseReleaseTag and ordering', () => {
  assert.deepEqual(parseReleaseTag('v0.7.5-rc.2'), { major: 0, minor: 7, patch: 5, rc: 2 })
  assert.equal(parseReleaseTag('v0.7.5-beta.1'), null)
  const sorted = ['v0.7.5-rc.2', 'v0.7.5', 'v0.7.5-rc.10', 'v0.7.4', 'v0.8.0-rc.1'].sort(compareReleaseTags)
  assert.deepEqual(sorted, ['v0.7.4', 'v0.7.5-rc.2', 'v0.7.5-rc.10', 'v0.7.5', 'v0.8.0-rc.1'])
})

test('selectReleaseTag per channel', () => {
  const tags = ['v0.7.4', 'v0.7.5-rc.1', 'v0.7.5-rc.2', 'junk', 'v0.7.5-beta.1']
  assert.equal(selectReleaseTag(tags, 'stable'), 'v0.7.4')
  assert.equal(selectReleaseTag(tags, 'tags'), 'v0.7.4')
  assert.equal(selectReleaseTag(tags, 'preview'), 'v0.7.5-rc.2')
  assert.equal(selectReleaseTag([...tags, 'v0.7.5'], 'preview'), 'v0.7.5')
  assert.equal(selectReleaseTag(tags, 'main'), '')
  assert.equal(selectReleaseTag([], 'stable'), '')
})

test('parseLsRemoteTags prefers the peeled commit of annotated tags', () => {
  const out = parseLsRemoteTags([
    'aaaa\trefs/tags/v0.7.4',
    'bbbb\trefs/tags/v0.7.4^{}',
    'cccc\trefs/tags/v0.7.5-rc.1',
    'dddd\trefs/heads/main',
    ''
  ].join('\n'))
  assert.deepEqual(out, { 'v0.7.4': 'bbbb', 'v0.7.5-rc.1': 'cccc' })
  assert.equal(versionFromTag('v0.7.5-rc.1'), '0.7.5-rc.1')
})
