/**
 * Release channels and release tags (AIS-292) — the desktop twin of
 * hermes_cli/release_channels.py. Pure helpers, no git, no network, so the
 * channel logic is unit-testable without booting Electron.
 *
 *   vX.Y.Z-rc.N  release candidate cut from main  (channel: preview)
 *   vX.Y.Z       stable release, promoted from a candidate (channel: stable)
 *
 * Channels: `stable` (alias `tags`) follows only vX.Y.Z; `preview` follows
 * the highest tag including candidates; `main` (or any branch name) follows
 * the branch.
 */

const STABLE_TAG_RE = /^v(\d+)\.(\d+)\.(\d+)$/
const RC_TAG_RE = /^v(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$/
const CHANNEL_ALIASES = { tags: 'stable', release: 'stable', rc: 'preview', beta: 'preview' }
const TAG_CHANNELS = ['stable', 'preview']

function normalizeChannel(name) {
  const value = String(name || '').trim()
  if (!value) return 'main'
  const lowered = value.toLowerCase()
  if (CHANNEL_ALIASES[lowered]) return CHANNEL_ALIASES[lowered]
  if (lowered === 'stable' || lowered === 'preview' || lowered === 'main') return lowered
  return value
}

function isTagChannel(name) {
  return TAG_CHANNELS.includes(normalizeChannel(name))
}

function parseReleaseTag(tag) {
  const value = String(tag || '').trim()
  let m = STABLE_TAG_RE.exec(value)
  if (m) return { major: +m[1], minor: +m[2], patch: +m[3], rc: null }
  m = RC_TAG_RE.exec(value)
  if (m) return { major: +m[1], minor: +m[2], patch: +m[3], rc: +m[4] }
  return null
}

function isStableTag(tag) {
  return STABLE_TAG_RE.test(String(tag || '').trim())
}

function releaseSortKey(tag) {
  const p = parseReleaseTag(tag)
  if (!p) return [-1, -1, -1, -1, -1]
  return [p.major, p.minor, p.patch, p.rc === null ? 1 : 0, p.rc || 0]
}

function compareReleaseTags(a, b) {
  const ka = releaseSortKey(a)
  const kb = releaseSortKey(b)
  for (let i = 0; i < ka.length; i += 1) {
    if (ka[i] !== kb[i]) return ka[i] - kb[i]
  }
  return 0
}

/** The tag an update should target for `channel`, or '' when nothing fits. */
function selectReleaseTag(tags, channel) {
  const normalized = normalizeChannel(channel)
  if (!TAG_CHANNELS.includes(normalized)) return ''
  let best = ''
  for (const raw of tags || []) {
    const tag = String(raw || '').trim()
    if (!parseReleaseTag(tag)) continue
    if (normalized === 'stable' && !isStableTag(tag)) continue
    if (!best || compareReleaseTags(tag, best) > 0) best = tag
  }
  return best
}

/**
 * Parse `git ls-remote --tags` output into `{ tagName: sha }`, preferring the
 * peeled commit (`refs/tags/v1^{}`) of annotated tags over the tag object.
 */
function parseLsRemoteTags(stdout) {
  const out = {}
  for (const rawLine of String(stdout || '').split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const [sha, ref] = line.split(/\s+/)
    if (!sha || !ref || !ref.startsWith('refs/tags/')) continue
    const peeled = ref.endsWith('^{}')
    const name = ref.slice('refs/tags/'.length).replace(/\^\{\}$/, '')
    if (peeled || !out[name]) out[name] = sha
  }
  return out
}

function versionFromTag(tag) {
  const value = String(tag || '').trim()
  return value.startsWith('v') ? value.slice(1) : value
}

module.exports = {
  TAG_CHANNELS,
  compareReleaseTags,
  isStableTag,
  isTagChannel,
  normalizeChannel,
  parseLsRemoteTags,
  parseReleaseTag,
  selectReleaseTag,
  versionFromTag
}
