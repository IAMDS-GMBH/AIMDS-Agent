// AIS-750: curated release notes instead of raw developer commit subjects.
const test = require('node:test')
const assert = require('node:assert/strict')

const {
  COMMIT_LOG_FORMAT,
  compareNoteVersions,
  isTestsOnly,
  manifestNotesToVersions,
  mergeReleaseNotes,
  normalizeNoteEntries,
  parseCommitLog,
  parseNoteVersion,
  releaseNotesFromManifest,
  selectNoteVersions,
  versionFromNoteFile
} = require('./release-notes.cjs')

const note = (group, text, extra = {}) => ({ group, de: `${text} (de)`, en: `${text} (en)`, ...extra })

test('parseNoteVersion: release, candidate, describe and junk', () => {
  assert.deepEqual(parseNoteVersion('0.7.13'), { major: 0, minor: 7, patch: 13, rc: null })
  assert.deepEqual(parseNoteVersion('v0.7.13-rc.3'), { major: 0, minor: 7, patch: 13, rc: 3 })
  assert.deepEqual(parseNoteVersion('0.7.12+6'), { major: 0, minor: 7, patch: 12, rc: null })
  assert.equal(parseNoteVersion('main'), null)
  assert.equal(parseNoteVersion(''), null)
})

test('compareNoteVersions: a candidate ranks below its own release', () => {
  assert.ok(compareNoteVersions('0.7.13-rc.1', '0.7.13') < 0)
  assert.ok(compareNoteVersions('0.7.13-rc.3', '0.7.13-rc.1') > 0)
  assert.ok(compareNoteVersions('0.7.10', '0.7.9') > 0)
  assert.equal(compareNoteVersions('v0.7.13', '0.7.13'), 0)
})

test('versionFromNoteFile: only X.Y.Z.json counts', () => {
  assert.equal(versionFromNoteFile('release-notes/0.7.13.json'), '0.7.13')
  assert.equal(versionFromNoteFile('0.7.14.json'), '0.7.14')
  assert.equal(versionFromNoteFile('release-notes/README.md'), null)
  assert.equal(versionFromNoteFile('release-notes/0.7.13-rc.1.json'), null)
})

test('normalizeNoteEntries: drops invalid entries, keeps channels', () => {
  const entries = normalizeNoteEntries({
    version: '0.7.13',
    notes: [
      note('new', 'Teams'),
      { group: 'other', de: 'x', en: 'x' },
      { group: 'fixed', de: 'only german' },
      { group: 'fixed', de: '  ', en: '' },
      { group: 'fixed', de: 'a'.repeat(400), en: 'x'.repeat(400) },
      'junk',
      note('fixed', 'Preview only', { channels: ['Preview', ''] })
    ]
  })
  assert.deepEqual(entries, [
    { group: 'new', de: 'Teams (de)', en: 'Teams (en)' },
    { group: 'fixed', de: 'only german', en: '' },
    { group: 'fixed', de: 'Preview only (de)', en: 'Preview only (en)', channels: ['preview'] }
  ])
  assert.deepEqual(normalizeNoteEntries([note('improved', 'Bare array')]).length, 1)
  assert.deepEqual(normalizeNoteEntries(null), [])
  assert.deepEqual(normalizeNoteEntries({ notes: 'nope' }), [])
})

test('selectNoteVersions: between current (exclusive) and target (inclusive), newest first', () => {
  const available = ['0.7.10', '0.7.11', '0.7.12', '0.7.13', '0.7.14']
  assert.deepEqual(selectNoteVersions(available, { current: '0.7.11', target: 'v0.7.13' }), ['0.7.13', '0.7.12'])
  // rc → rc of the same version: the version's notes are the update.
  assert.deepEqual(selectNoteVersions(available, { current: '0.7.13-rc.1', target: 'v0.7.13-rc.3' }), ['0.7.13'])
  // dev checkout past a tag
  assert.deepEqual(selectNoteVersions(available, { current: '0.7.12+6', target: '0.7.13' }), ['0.7.13'])
  // unknown current: only the target's own notes
  assert.deepEqual(selectNoteVersions(available, { current: '', target: '0.7.13' }), ['0.7.13'])
  // missing file for the target: nothing newer than current exists either
  assert.deepEqual(selectNoteVersions(['0.7.10'], { current: '0.7.12', target: '0.7.13' }), [])
  assert.deepEqual(selectNoteVersions(available, { current: '0.7.1', target: '0.7.14', limit: 2 }), ['0.7.14', '0.7.13'])
  assert.deepEqual(selectNoteVersions(available, { current: '0.7.11', target: 'main' }), [])
})

test('mergeReleaseNotes: newest first, channel filter, dedupe', () => {
  const merged = mergeReleaseNotes(
    [
      { version: '0.7.12', entries: [note('fixed', 'Old fix'), note('fixed', 'Shared')] },
      {
        version: '0.7.13',
        entries: [note('new', 'New thing'), note('fixed', 'Shared'), note('fixed', 'Preview only', { channels: ['preview'] })]
      }
    ],
    { channel: 'stable' }
  )
  assert.deepEqual(
    merged.map(n => `${n.version}:${n.en}`),
    ['0.7.13:New thing (en)', '0.7.13:Shared (en)', '0.7.12:Old fix (en)']
  )
  const preview = mergeReleaseNotes([{ version: '0.7.13', entries: [note('fixed', 'Preview only', { channels: ['preview'] })] }], {
    channel: 'preview'
  })
  assert.equal(preview.length, 1)
  assert.equal('channels' in preview[0], false)
  assert.deepEqual(mergeReleaseNotes([], { channel: 'stable' }), [])
})

test('manifest notes: malformed values never break, selection follows the marker', () => {
  assert.deepEqual(manifestNotesToVersions(undefined), [])
  assert.deepEqual(manifestNotesToVersions([note('new', 'x')]), [])
  assert.deepEqual(manifestNotesToVersions({ '0.7.13-rc.1': [note('new', 'x')], junk: [] }), [])
  const notes = {
    '0.7.12': [note('fixed', 'Twelve')],
    '0.7.13': [note('new', 'Thirteen')],
    '0.7.11': [note('fixed', 'Eleven')]
  }
  assert.deepEqual(
    releaseNotesFromManifest(notes, { current: '0.7.11', target: '0.7.13', channel: 'stable' }).map(n => n.en),
    ['Thirteen (en)', 'Twelve (en)']
  )
  assert.deepEqual(releaseNotesFromManifest(notes, { current: '0.7.13', target: '0.7.13', channel: 'stable' }), [])
})

test('parseCommitLog: header fields and a tests-only flag from --name-only', () => {
  assert.ok(COMMIT_LOG_FORMAT.startsWith('--pretty=format:\x1e%H'))
  const stdout = [
    '\x1eaaa\x1ffix(AIS-728): test runs stay hermetic (#232)\x1fdev\x1f1761600000',
    'tests/conftest.py',
    'tests/test_hermetic_test_logs.py',
    '',
    '\x1ebbb\x1ffeat(AIS-737): teams channels (#230)\x1fdev\x1f1761500000',
    'optional-mcps/MSOffice365MCP/server.py',
    'tests/test_teams.py',
    '',
    '\x1eccc\x1fchore: empty\x1fdev\x1f1761400000'
  ].join('\n')
  const commits = parseCommitLog(stdout)
  assert.deepEqual(
    commits.map(c => [c.sha, c.testsOnly]),
    [['aaa', true], ['bbb', false], ['ccc', false]]
  )
  assert.equal(commits[0].summary, 'fix(AIS-728): test runs stay hermetic (#232)')
  assert.equal(commits[0].at, 1761600000 * 1000)
  assert.deepEqual(parseCommitLog(''), [])
})

test('isTestsOnly: every path must be a test file', () => {
  assert.equal(isTestsOnly(['apps/desktop/src/lib/x.test.ts', 'apps/desktop/electron/y.test.cjs']), true)
  assert.equal(isTestsOnly(['tests/conftest.py', 'agent/x.py']), false)
  assert.equal(isTestsOnly([]), false)
})
