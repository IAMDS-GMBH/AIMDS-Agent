// Release channels (AIS-292): stable follows vX.Y.Z, preview also the
// vX.Y.Z-rc.N candidates; selection is semver, not ls-remote order.
const test = require('node:test')
const assert = require('node:assert/strict')

const {
  compareReleaseTags,
  headReleaseTag,
  isTagChannel,
  normalizeChannel,
  parseLsRemoteTags,
  parseReleaseTag,
  releaseTagIsNewer,
  resolveTagChannelStatus,
  selectReleaseTag,
  versionFromTag
} = require('./update-channels.cjs')

test('normalizeChannel maps aliases and keeps branch names', () => {
  assert.equal(normalizeChannel(''), 'main')
  assert.equal(normalizeChannel('tags'), 'stable')
  assert.equal(normalizeChannel('Preview'), 'preview')
  assert.equal(normalizeChannel('bb/gui'), 'bb/gui')
  assert.equal(normalizeChannel('Auto'), 'auto')
  assert.equal(isTagChannel('tags'), true)
  assert.equal(isTagChannel('main'), false)
  assert.equal(isTagChannel('auto'), false)
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

// AIS-297 / SUP-20260907-101225: the tag-channel check must never turn a
// checkout that is *past* the release (or one whose tag fetch failed) into a
// permanent "+1 update".

test('resolveTagChannelStatus: HEAD on the release tag is up to date', () => {
  assert.deepEqual(
    resolveTagChannelStatus({ currentSha: 'aaa', targetSha: 'aaa', behindCount: 0, aheadCount: 0 }),
    { behind: 0, aheadOfTarget: 0, offChannel: false }
  )
  // Sha equality wins even when the counts could not be computed.
  assert.deepEqual(
    resolveTagChannelStatus({ currentSha: 'aaa', targetSha: 'aaa', behindCount: null, aheadCount: null }),
    { behind: 0, aheadOfTarget: 0, offChannel: false }
  )
})

test('resolveTagChannelStatus: release ahead of HEAD is a real update with a count', () => {
  assert.deepEqual(
    resolveTagChannelStatus({ currentSha: 'aaa', targetSha: 'bbb', behindCount: 7, aheadCount: 0 }),
    { behind: 7, aheadOfTarget: 0, offChannel: false }
  )
})

test('resolveTagChannelStatus: HEAD past the release is off-channel, not "+1"', () => {
  // main checkout 92 commits after v0.7.4 on the stable channel — the exact
  // state from the support case.
  assert.deepEqual(
    resolveTagChannelStatus({ currentSha: 'da25225', targetSha: '3ea1f3c', behindCount: 0, aheadCount: 92 }),
    { behind: 0, aheadOfTarget: 92, offChannel: true }
  )
})

test('resolveTagChannelStatus: unresolvable target (tag fetch failed) is an error, not an update', () => {
  const status = resolveTagChannelStatus({ currentSha: 'da25225', targetSha: '3ea1f3c', behindCount: null, aheadCount: null })
  assert.equal(status.error, 'fetch-failed')
  assert.equal(status.behind, 0)
  assert.equal(status.offChannel, false)
})

// AIS-299 / SUP-20260907: a checkout sitting on a release tag that is *newer*
// than the channel's target (v0.7.5-rc.1 while stable is still v0.7.4) is not
// a dev checkout — no "switch to v0.7.4" offer, which would be a downgrade.

test('headReleaseTag picks the highest release tag on HEAD', () => {
  assert.equal(headReleaseTag(['nightly-1', 'v0.7.5-rc.1']), 'v0.7.5-rc.1')
  assert.equal(headReleaseTag(['v0.7.5-rc.2', 'v0.7.5']), 'v0.7.5')
  assert.equal(headReleaseTag(['nightly-1']), '')
  assert.equal(headReleaseTag([]), '')
  assert.equal(releaseTagIsNewer('v0.7.5-rc.1', 'v0.7.4'), true)
  assert.equal(releaseTagIsNewer('v0.7.5-rc.1', 'v0.7.5-rc.2'), false)
  assert.equal(releaseTagIsNewer('', 'v0.7.4'), false)
})

test('resolveTagChannelStatus: HEAD on a newer candidate than the stable target is not off-channel', () => {
  assert.deepEqual(
    resolveTagChannelStatus({
      currentSha: 'f96a0ed',
      targetSha: '3ea1f3c',
      behindCount: 0,
      aheadCount: 94,
      headTags: ['v0.7.5-rc.1'],
      targetTag: 'v0.7.4'
    }),
    { behind: 0, aheadOfTarget: 94, offChannel: false, newerThanTarget: true, headTag: 'v0.7.5-rc.1' }
  )
  // Even when the tag fetch failed: the local tag on HEAD is enough to know.
  const status = resolveTagChannelStatus({
    currentSha: 'f96a0ed',
    targetSha: '3ea1f3c',
    behindCount: null,
    aheadCount: null,
    headTags: ['v0.7.5-rc.1'],
    targetTag: 'v0.7.4'
  })
  assert.equal(status.newerThanTarget, true)
  assert.equal(status.error, undefined)
})

test('resolveTagChannelStatus: promoted stable on the same commit is up to date; older candidate updates', () => {
  assert.deepEqual(
    resolveTagChannelStatus({
      currentSha: 'same',
      targetSha: 'same',
      behindCount: 0,
      aheadCount: 0,
      headTags: ['v0.7.5-rc.2', 'v0.7.5'],
      targetTag: 'v0.7.5'
    }),
    { behind: 0, aheadOfTarget: 0, offChannel: false }
  )
  assert.deepEqual(
    resolveTagChannelStatus({
      currentSha: 'rc1',
      targetSha: 'rc2',
      behindCount: 3,
      aheadCount: 0,
      headTags: ['v0.7.5-rc.1'],
      targetTag: 'v0.7.5-rc.2'
    }),
    { behind: 3, aheadOfTarget: 0, offChannel: false }
  )
})
