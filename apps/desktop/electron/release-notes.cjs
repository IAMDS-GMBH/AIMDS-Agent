/**
 * Curated end-user release notes for the updates overlay (AIS-750).
 *
 * The overlay used to list raw squash-commit subjects: English, PR numbers,
 * test-only changes. Releases now carry `release-notes/<X.Y.Z>.json` in the
 * repository (de + en per entry, written by hand per release); git installs
 * read the files from the target commit, release installs get them through
 * the `notes` field of `hermes-release.json` (scripts/release_notes.py).
 *
 * Pure helpers — no git, no network — so the selection, merge and commit-log
 * parsing are unit-testable without Electron. Keep the entry rules in sync
 * with scripts/release_notes.py.
 *
 *   release-notes/0.7.13.json
 *   { "version": "0.7.13",
 *     "notes": [ { "group": "new", "de": "…", "en": "…" },
 *                { "group": "fixed", "de": "…", "en": "…", "channels": ["preview"] } ] }
 */

const RELEASE_NOTES_DIR = 'release-notes'
const NOTE_GROUPS = ['new', 'improved', 'fixed']
const NOTE_FILE_RE = /^(\d+)\.(\d+)\.(\d+)\.json$/
const VERSION_RE = /^v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?(?:\+(\d+))?$/
const MAX_NOTE_LENGTH = 300
const MAX_ENTRIES_PER_VERSION = 40
const MAX_MERGED_ENTRIES = 24
// Versions a single dialog reads at most: a client months behind still gets
// a bounded number of `git show` calls and a list that fits the dialog.
const MAX_VERSIONS = 8

/**
 * `0.7.13`, `v0.7.13-rc.2` and `0.7.12+6` (a checkout 6 commits past the
 * tag, resolveReleaseDescribeVersion) → `{ major, minor, patch, rc }`.
 * `null` for anything else.
 */
function parseNoteVersion(value) {
  const m = VERSION_RE.exec(String(value || '').trim())
  if (!m) return null
  return { major: +m[1], minor: +m[2], patch: +m[3], rc: m[4] === undefined ? null : +m[4] }
}

// A candidate ranks below the release of its own version (0.7.13-rc.1 < 0.7.13).
function versionKey(parsed) {
  return [parsed.major, parsed.minor, parsed.patch, parsed.rc === null ? 1 : 0, parsed.rc || 0]
}

function compareNoteVersions(a, b) {
  const pa = typeof a === 'string' ? parseNoteVersion(a) : a
  const pb = typeof b === 'string' ? parseNoteVersion(b) : b
  if (!pa || !pb) return 0
  const ka = versionKey(pa)
  const kb = versionKey(pb)
  for (let i = 0; i < ka.length; i += 1) {
    if (ka[i] !== kb[i]) return ka[i] - kb[i]
  }
  return 0
}

function baseVersion(value) {
  const parsed = parseNoteVersion(value)
  return parsed ? `${parsed.major}.${parsed.minor}.${parsed.patch}` : null
}

/** `release-notes/0.7.13.json` (or a bare file name) → `0.7.13`, else null. */
function versionFromNoteFile(name) {
  const file = String(name || '').trim().split('/').pop()
  const m = NOTE_FILE_RE.exec(file || '')
  return m ? `${+m[1]}.${+m[2]}.${+m[3]}` : null
}

function cleanText(value) {
  if (typeof value !== 'string') return ''
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > MAX_NOTE_LENGTH ? '' : text
}

/**
 * Validated entries of one notes file (or of one manifest `notes` value).
 * Accepts `{ notes: [...] }` or a bare array. Invalid entries are dropped,
 * never fatal — notes are cosmetic and must not wedge an update check.
 */
function normalizeNoteEntries(raw) {
  const list = Array.isArray(raw) ? raw : raw && typeof raw === 'object' && Array.isArray(raw.notes) ? raw.notes : []
  const entries = []
  for (const item of list) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const group = String(item.group || '').trim().toLowerCase()
    if (!NOTE_GROUPS.includes(group)) continue
    // One language is enough: the dialog falls back to the other one.
    const de = cleanText(item.de)
    const en = cleanText(item.en)
    if (!de && !en) continue
    const entry = { group, de, en }
    if (Array.isArray(item.channels)) {
      const channels = item.channels.map(c => String(c || '').trim().toLowerCase()).filter(Boolean)
      if (channels.length) entry.channels = channels
    }
    entries.push(entry)
    if (entries.length >= MAX_ENTRIES_PER_VERSION) break
  }
  return entries
}

/**
 * The versions whose notes describe an update from `current` to `target`:
 * newer than `current` (exclusive), up to the base version of `target`
 * (inclusive), newest first. A candidate counts below its own release, so
 * 0.7.13-rc.1 → 0.7.13-rc.3 shows the 0.7.13 notes. Unknown `current` →
 * the target's own version only.
 */
function selectNoteVersions(available, { current, target, limit = MAX_VERSIONS } = {}) {
  const targetBase = baseVersion(target)
  if (!targetBase) return []
  const currentParsed = parseNoteVersion(current)
  const seen = new Set()
  const picked = []
  for (const value of available || []) {
    const version = baseVersion(value)
    if (!version || seen.has(version)) continue
    seen.add(version)
    if (compareNoteVersions(version, targetBase) > 0) continue
    if (currentParsed) {
      if (compareNoteVersions(version, currentParsed) <= 0) continue
    } else if (version !== targetBase) {
      continue
    }
    picked.push(version)
  }
  picked.sort((a, b) => compareNoteVersions(b, a))
  return picked.slice(0, limit)
}

function noteChannelMatches(entry, channel) {
  if (!entry.channels) return true
  return entry.channels.includes(String(channel || '').trim().toLowerCase())
}

/**
 * Merge per-version notes into one list for the dialog: newest version first,
 * entries of other channels dropped, duplicates (same de or en text) kept
 * once. `perVersion` = `[{ version, entries }]` in any order.
 */
function mergeReleaseNotes(perVersion, { channel, limit = MAX_MERGED_ENTRIES } = {}) {
  const ordered = [...(perVersion || [])]
    .filter(item => item && parseNoteVersion(item.version))
    .sort((a, b) => compareNoteVersions(b.version, a.version))
  const seen = new Set()
  const merged = []
  for (const { version, entries } of ordered) {
    for (const entry of normalizeNoteEntries(entries)) {
      if (!noteChannelMatches(entry, channel)) continue
      const keys = [entry.de && `de:${entry.de.toLowerCase()}`, entry.en && `en:${entry.en.toLowerCase()}`].filter(Boolean)
      if (keys.some(key => seen.has(key))) continue
      keys.forEach(key => seen.add(key))
      merged.push({ group: entry.group, de: entry.de, en: entry.en, version: baseVersion(version) })
      if (merged.length >= limit) return merged
    }
  }
  return merged
}

/**
 * The manifest's optional `notes` field (scripts/release_notes.py):
 * `{ "0.7.13": [entries], "0.7.12": [entries] }` → `[{ version, entries }]`.
 * Anything malformed yields `[]`; the manifest itself stays valid.
 */
function manifestNotesToVersions(notes) {
  if (!notes || typeof notes !== 'object' || Array.isArray(notes)) return []
  const out = []
  for (const [version, entries] of Object.entries(notes)) {
    if (!baseVersion(version) || baseVersion(version) !== version) continue
    const normalized = normalizeNoteEntries(entries)
    if (normalized.length) out.push({ version, entries: normalized })
  }
  return out.sort((a, b) => compareNoteVersions(b.version, a.version)).slice(0, MAX_VERSIONS)
}

/** Notes for a release install: manifest notes between marker and target. */
function releaseNotesFromManifest(manifestNotes, { current, target, channel }) {
  const perVersion = Array.isArray(manifestNotes) ? manifestNotes : manifestNotesToVersions(manifestNotes)
  const wanted = new Set(selectNoteVersions(perVersion.map(v => v.version), { current, target }))
  return mergeReleaseNotes(perVersion.filter(v => wanted.has(v.version)), { channel })
}

// ---------------------------------------------------------------------------
// Commit log for the developer fallback (main channel, versions without notes)
// ---------------------------------------------------------------------------

const COMMIT_FIELD_SEP = '\x1f'
const COMMIT_RECORD_SEP = '\x1e'
// `git log --name-only` with this format: a record separator, the header
// fields, then one changed path per line.
const COMMIT_LOG_FORMAT = `--pretty=format:${COMMIT_RECORD_SEP}%H${COMMIT_FIELD_SEP}%s${COMMIT_FIELD_SEP}%an${COMMIT_FIELD_SEP}%at`

const TEST_PATH_RES = [
  /(^|\/)tests?\//,
  /(^|\/)__tests__\//,
  /(^|\/)conftest\.py$/,
  /(^|\/)test_[^/]*\.py$/,
  /_test\.py$/,
  /\.(test|spec)\.[cm]?[jt]sx?$/
]

function isTestPath(file) {
  return TEST_PATH_RES.some(re => re.test(file))
}

/** True when every changed path is a test file — nothing a user can notice. */
function isTestsOnly(files) {
  const list = (files || []).map(f => String(f || '').trim()).filter(Boolean)
  return list.length > 0 && list.every(isTestPath)
}

/**
 * Parse `git log COMMIT_LOG_FORMAT --name-only` output into
 * `[{ sha, summary, author, at, testsOnly }]`.
 */
function parseCommitLog(stdout) {
  return String(stdout || '')
    .split(COMMIT_RECORD_SEP)
    .map(chunk => chunk.replace(/^\r?\n+/, ''))
    .filter(chunk => chunk.trim())
    .map(chunk => {
      const lines = chunk.split(/\r?\n/)
      const [sha, summary, author, at] = lines[0].split(COMMIT_FIELD_SEP)
      const files = lines.slice(1).map(l => l.trim()).filter(Boolean)
      return {
        sha: (sha || '').trim(),
        summary: summary || '',
        author: author || '',
        at: (Number.parseInt(at, 10) || 0) * 1000,
        testsOnly: isTestsOnly(files)
      }
    })
    .filter(commit => commit.sha)
}

module.exports = {
  COMMIT_LOG_FORMAT,
  MAX_VERSIONS,
  NOTE_GROUPS,
  RELEASE_NOTES_DIR,
  baseVersion,
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
}
