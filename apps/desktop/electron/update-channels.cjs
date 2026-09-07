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

/**
 * Decide what a tag-channel check (stable/preview) reports, from the raw
 * git facts. Pure so the loop from SUP-20260907-101225 (AIS-297) stays
 * covered by a unit test:
 *
 *   - HEAD on the target tag              → up to date (behind 0)
 *   - target tag reachable ahead of HEAD  → behind N, changelog available
 *   - HEAD *past* the tag (dev / main checkout on a release channel)
 *                                         → offChannel, aheadOfTarget N,
 *                                            behind 0 — a "switch to the
 *                                            release" offer, never a phantom
 *                                            "+1 update"
 *   - target not resolvable locally       → error 'fetch-failed' — the tag
 *     (fetch failed, no counts)              fetch must succeed before any
 *                                            offer is made
 *
 * `behindCount` / `aheadCount` are the `git rev-list` counts HEAD..tag and
 * tag..HEAD, or null when git could not resolve the tag.
 */
function resolveTagChannelStatus({ currentSha, targetSha, behindCount, aheadCount }) {
  const behind = Number.isFinite(behindCount) ? Math.max(0, behindCount) : null
  const ahead = Number.isFinite(aheadCount) ? Math.max(0, aheadCount) : null
  if (currentSha && targetSha && currentSha === targetSha) {
    return { behind: 0, aheadOfTarget: 0, offChannel: false }
  }
  if (behind === null) {
    return { behind: 0, aheadOfTarget: 0, offChannel: false, error: 'fetch-failed' }
  }
  if (behind > 0) {
    return { behind, aheadOfTarget: ahead || 0, offChannel: false }
  }
  // Same or unrelated history with nothing to pull: HEAD is past the tag.
  return { behind: 0, aheadOfTarget: ahead || 0, offChannel: true }
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
  resolveTagChannelStatus,
  selectReleaseTag,
  versionFromTag
}
