// Release channels (AIS-292): stable follows vX.Y.Z, preview also the
// vX.Y.Z-rc.N candidates; selection is semver, not ls-remote order.
const test = require('node:test')
const assert = require('node:assert/strict')

const {
  RELEASE_MANIFEST_ASSET,
  RELEASE_REPO,
  compareReleaseTags,
  headReleaseTag,
  isTagChannel,
  latestManifestUrl,
  normalizeChannel,
  parseLsRemoteTags,
  parseReleaseTag,
  readReleaseMarker,
  releaseDownloadUrl,
  releaseTagIsNewer,
  resolveReleaseStatus,
  resolveTagChannelStatus,
  selectReleaseFromApi,
  selectReleaseTag,
  tagFitsChannel,
  validateReleaseManifest,
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

// ---------------------------------------------------------------------------
// Release archives (AIS-312): manifest + marker validation mirrors
// hermes_cli/release_update.py — the same manifest must pass or fail on both
// sides.
// ---------------------------------------------------------------------------

const COMMIT = 'a'.repeat(40)
const SHA256 = 'b'.repeat(64)

function manifest(overrides = {}) {
  return {
    format: 'hermes-release-v1',
    version: '0.7.6',
    tag: 'v0.7.6',
    commit_sha: COMMIT,
    source_archive: 'hermes-source-0.7.6.zip',
    sha256: SHA256,
    size: 39812345,
    built_at: '2026-09-08T10:00:00+00:00',
    ...overrides
  }
}

function rcManifest(overrides = {}) {
  return manifest({ version: '0.7.6-rc.1', tag: 'v0.7.6-rc.1', source_archive: 'hermes-source-0.7.6-rc.1.zip', ...overrides })
}

test('tagFitsChannel: stable only vX.Y.Z, preview also candidates, branches never', () => {
  assert.equal(tagFitsChannel('v0.7.6', 'stable'), true)
  assert.equal(tagFitsChannel('v0.7.6-rc.1', 'stable'), false)
  assert.equal(tagFitsChannel('v0.7.6', 'preview'), true)
  assert.equal(tagFitsChannel('v0.7.6-rc.1', 'preview'), true)
  assert.equal(tagFitsChannel('v0.7.6', 'tags'), true)
  assert.equal(tagFitsChannel('v0.7.6', 'main'), false)
  assert.equal(tagFitsChannel('junk', 'preview'), false)
})

test('release URLs', () => {
  assert.equal(RELEASE_REPO, 'IAMDS-GMBH/AIMDS-Agent-Releases')
  assert.equal(
    releaseDownloadUrl('v0.7.6', 'hermes-source-0.7.6.zip'),
    'https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/download/v0.7.6/hermes-source-0.7.6.zip'
  )
  assert.equal(latestManifestUrl(), `https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/latest/download/${RELEASE_MANIFEST_ASSET}`)
  assert.equal(latestManifestUrl('acme/other'), 'https://github.com/acme/other/releases/latest/download/hermes-release.json')
})

test('validateReleaseManifest accepts a stable manifest on stable and preview, a candidate on preview', () => {
  const stable = validateReleaseManifest(manifest(), { channel: 'stable' })
  assert.equal(stable.ok, true)
  assert.equal(stable.manifest.tag, 'v0.7.6')
  assert.equal(stable.manifest.version, '0.7.6')
  assert.equal(stable.manifest.channel, 'stable')
  assert.equal(stable.manifest.build_id, '2026-09-08T10:00:00+00:00')
  assert.equal(
    stable.manifest.package_url,
    'https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/download/v0.7.6/hermes-source-0.7.6.zip'
  )
  assert.equal(validateReleaseManifest(manifest(), { channel: 'preview' }).ok, true)
  assert.equal(validateReleaseManifest(rcManifest(), { channel: 'preview' }).ok, true)
  // Unknown fields are ignored; `Z` timestamps parse too.
  assert.equal(validateReleaseManifest(manifest({ extra: 1, built_at: '2026-09-08T10:00:00Z' }), { channel: 'stable' }).ok, true)
  // The GitHub release the manifest came from must match its tag.
  assert.equal(validateReleaseManifest(manifest(), { channel: 'stable', releaseTag: 'v0.7.6' }).ok, true)
  assert.match(validateReleaseManifest(manifest(), { channel: 'stable', releaseTag: 'v0.7.5' }).error, /does not match release/)
})

test('validateReleaseManifest rejects malformed manifests with a stable reason', () => {
  const reject = (m, channel, pattern) => {
    const result = validateReleaseManifest(m, { channel })
    assert.equal(result.ok, false, `expected rejection for ${JSON.stringify(m)}`)
    assert.match(result.error, pattern)
  }
  reject(null, 'stable', /not a JSON object/)
  reject(manifest({ format: 'hermes-release-v2' }), 'stable', /unsupported manifest format/)
  reject(rcManifest(), 'stable', /not a stable release/)
  reject(manifest({ version: '0.7.5' }), 'stable', /does not match tag/)
  reject(manifest({ commit_sha: 'a'.repeat(39) }), 'stable', /commit_sha/)
  reject(manifest({ commit_sha: 'A'.repeat(40) }), 'stable', /commit_sha/)
  reject(manifest({ sha256: 'b'.repeat(63) }), 'stable', /sha256/)
  reject(manifest({ source_archive: 'hermes-source-0.7.5.zip' }), 'stable', /source_archive/)
  reject(manifest({ source_archive: '../hermes-source-0.7.6.zip' }), 'stable', /source_archive/)
  reject(manifest({ size: 0 }), 'stable', /size/)
  reject(manifest({ size: '39812345' }), 'stable', /size/)
  reject(manifest({ size: 1.5 }), 'stable', /size/)
  reject(manifest({ built_at: undefined }), 'stable', /built_at/)
  reject(manifest({ built_at: 'yesterday' }), 'stable', /built_at/)
  reject(manifest({ tag: 'v0.7.6-beta.1', version: '0.7.6-beta.1' }), 'preview', /not a release tag/)
  reject(manifest(), 'main', /not a main release/)
})

test('readReleaseMarker: absent, unreadable or malformed markers read as null', () => {
  const enoent = () => {
    const err = new Error('ENOENT')
    err.code = 'ENOENT'
    throw err
  }
  assert.equal(readReleaseMarker('/tmp/x', { readFile: enoent }), null)
  assert.equal(readReleaseMarker('/tmp/x', { readFile: () => '{not json' }), null)
  assert.equal(readReleaseMarker('/tmp/x', { readFile: () => JSON.stringify({ format: 'hermes-release-v1', tag: 'v0.7.6', version: '0.7.6', commit_sha: COMMIT, channel: 'stable' }) }), null)
  assert.equal(readReleaseMarker('/tmp/x', { readFile: () => JSON.stringify({ format: 'hermes-release-marker-v1', tag: 'v0.7.6', version: '0.7.5', commit_sha: COMMIT, channel: 'stable' }) }), null)
  assert.equal(readReleaseMarker('/tmp/x', { readFile: () => JSON.stringify({ format: 'hermes-release-marker-v1', tag: 'v0.7.6', version: '0.7.6', commit_sha: 'abc', channel: 'stable' }) }), null)
  assert.equal(readReleaseMarker('/tmp/x', { readFile: () => JSON.stringify({ format: 'hermes-release-marker-v1', tag: 'v0.7.6', version: '0.7.6', commit_sha: COMMIT }) }), null)
})

test('readReleaseMarker: a valid marker yields the install identity', () => {
  let seenPath = ''
  const readFile = p => {
    seenPath = p
    return JSON.stringify({
      format: 'hermes-release-marker-v1',
      channel: 'tags',
      tag: 'v0.7.6',
      version: '0.7.6',
      commit_sha: COMMIT,
      sha256: SHA256,
      build_id: '2026-09-08T10:00:00+00:00',
      applied_at: '2026-09-08T11:00:00+00:00'
    })
  }
  assert.deepEqual(readReleaseMarker('/opt/hermes', { readFile }), {
    version: '0.7.6',
    tag: 'v0.7.6',
    commitSha: COMMIT,
    sha256: SHA256,
    buildId: '2026-09-08T10:00:00+00:00',
    appliedAt: '2026-09-08T11:00:00+00:00',
    channel: 'stable'
  })
  assert.match(seenPath, /[\\/]\.hermes-release\.json$/)
})

test('resolveReleaseStatus: the commit is authoritative', () => {
  // rc.2 promoted to v0.7.6 on the same commit: nothing to install.
  assert.deepEqual(
    resolveReleaseStatus({ markerTag: 'v0.7.6-rc.2', markerCommit: COMMIT, targetTag: 'v0.7.6', targetCommit: COMMIT }),
    { behind: 0, newerThanTarget: false, headTag: 'v0.7.6-rc.2' }
  )
  // Newer target: one release to install.
  assert.deepEqual(
    resolveReleaseStatus({ markerTag: 'v0.7.5', markerCommit: COMMIT, targetTag: 'v0.7.6', targetCommit: 'c'.repeat(40) }),
    { behind: 1, newerThanTarget: false, headTag: 'v0.7.5' }
  )
  // rc install on the stable channel while stable is older: never a downgrade.
  assert.deepEqual(
    resolveReleaseStatus({ markerTag: 'v0.7.6-rc.1', markerCommit: COMMIT, targetTag: 'v0.7.5', targetCommit: 'c'.repeat(40) }),
    { behind: 0, newerThanTarget: true, headTag: 'v0.7.6-rc.1' }
  )
  // Same tag, re-cut on a different commit: re-apply (rewrites the marker).
  assert.deepEqual(
    resolveReleaseStatus({ markerTag: 'v0.7.6', markerCommit: COMMIT, targetTag: 'v0.7.6', targetCommit: 'c'.repeat(40) }),
    { behind: 1, newerThanTarget: false, headTag: 'v0.7.6' }
  )
})

test('selectReleaseFromApi: drafts skipped, stable skips prereleases, highest tag wins', () => {
  const asset = tag => ({ name: 'hermes-release.json', browser_download_url: `https://github.com/x/y/releases/download/${tag}/hermes-release.json` })
  const releases = [
    { tag_name: 'v0.7.5-rc.2', prerelease: true, draft: false, assets: [asset('v0.7.5-rc.2')] },
    { tag_name: 'v0.7.5-rc.10', prerelease: true, draft: false, assets: [asset('v0.7.5-rc.10')] },
    { tag_name: 'v0.7.4', prerelease: false, draft: false, assets: [asset('v0.7.4'), { name: 'hermes-source-0.7.4.zip', browser_download_url: 'https://x/z.zip' }] },
    { tag_name: 'v0.7.6', prerelease: false, draft: true, assets: [asset('v0.7.6')] },
    { tag_name: 'nightly', prerelease: true, draft: false, assets: [asset('nightly')] }
  ]
  assert.deepEqual(selectReleaseFromApi(releases, 'preview'), {
    tag: 'v0.7.5-rc.10',
    manifestUrl: 'https://github.com/x/y/releases/download/v0.7.5-rc.10/hermes-release.json'
  })
  assert.deepEqual(selectReleaseFromApi(releases, 'stable'), {
    tag: 'v0.7.4',
    manifestUrl: 'https://github.com/x/y/releases/download/v0.7.4/hermes-release.json'
  })
  // A stable tag flagged prerelease is not offered on stable.
  assert.equal(selectReleaseFromApi([{ tag_name: 'v0.7.4', prerelease: true, assets: [asset('v0.7.4')] }], 'stable'), null)
  // The chosen release without a manifest asset is not usable.
  assert.equal(selectReleaseFromApi([{ tag_name: 'v0.7.4', prerelease: false, assets: [] }], 'stable'), null)
  assert.equal(selectReleaseFromApi([], 'preview'), null)
  assert.equal(selectReleaseFromApi(releases, 'main'), null)
})
