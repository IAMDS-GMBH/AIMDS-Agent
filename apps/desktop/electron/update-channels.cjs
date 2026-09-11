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
 * the branch. `auto` is the `updates.channel` config sentinel (AIS-299):
 * stable on a detached (release-tag) checkout, main on a named branch.
 */

const fs = require('node:fs')
const path = require('node:path')

const STABLE_TAG_RE = /^v(\d+)\.(\d+)\.(\d+)$/
const RC_TAG_RE = /^v(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$/
const CHANNEL_ALIASES = { tags: 'stable', release: 'stable', rc: 'preview', beta: 'preview' }
const TAG_CHANNELS = ['stable', 'preview']

// Release archives (AIS-312) — the desktop twin of hermes_cli/release_channels.py
// and hermes_cli/release_update.py. Releases are mirrored into a public repo;
// each release tag carries a `hermes-release.json` manifest next to the
// source archive, and an install applied from such an archive carries a
// `.hermes-release.json` marker in its root.
const RELEASE_REPO = 'IAMDS-GMBH/AIMDS-Agent-Releases'
const RELEASE_MANIFEST_FORMAT = 'hermes-release-v1'
const RELEASE_MARKER_FORMAT = 'hermes-release-marker-v1'
const RELEASE_MANIFEST_ASSET = 'hermes-release.json'
const RELEASE_MARKER_FILE = '.hermes-release.json'
const COMMIT_SHA_RE = /^[0-9a-f]{40}$/
const SHA256_RE = /^[0-9a-f]{64}$/

function normalizeChannel(name) {
  const value = String(name || '').trim()
  if (!value) return 'main'
  const lowered = value.toLowerCase()
  if (CHANNEL_ALIASES[lowered]) return CHANNEL_ALIASES[lowered]
  if (lowered === 'stable' || lowered === 'preview' || lowered === 'main' || lowered === 'auto') return lowered
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
 * The highest *release* tag among the tags pointing at HEAD (`git tag
 * --points-at HEAD`), or '' — non-release tags are ignored; a promoted commit
 * carrying both vX.Y.Z-rc.N and vX.Y.Z reports the stable tag.
 */
function headReleaseTag(tags) {
  let best = ''
  for (const raw of tags || []) {
    const tag = String(raw || '').trim()
    if (!parseReleaseTag(tag)) continue
    if (!best || compareReleaseTags(tag, best) > 0) best = tag
  }
  return best
}

/** True iff `candidate` is a release tag sorting strictly above `target`. */
function releaseTagIsNewer(candidate, target) {
  if (!candidate || !parseReleaseTag(candidate) || !parseReleaseTag(target)) return false
  return compareReleaseTags(candidate, target) > 0
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
 *   - HEAD on a release tag *newer* than  → newerThanTarget, behind 0, no
 *     the target (v0.7.5-rc.1 on stable     offer at all: the channel simply
 *     while v0.7.4 is the latest stable)     has nothing newer yet (AIS-299,
 *                                            SUP-20260907 — never a downgrade)
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
 * tag..HEAD, or null when git could not resolve the tag. `headTags` are the
 * tags pointing at HEAD, `targetTag` the channel's selected tag.
 */
function resolveTagChannelStatus({ currentSha, targetSha, behindCount, aheadCount, headTags, targetTag }) {
  const behind = Number.isFinite(behindCount) ? Math.max(0, behindCount) : null
  const ahead = Number.isFinite(aheadCount) ? Math.max(0, aheadCount) : null
  if (currentSha && targetSha && currentSha === targetSha) {
    return { behind: 0, aheadOfTarget: 0, offChannel: false }
  }
  const headTag = headReleaseTag(headTags)
  if (targetTag && releaseTagIsNewer(headTag, targetTag)) {
    return { behind: 0, aheadOfTarget: ahead || 0, offChannel: false, newerThanTarget: true, headTag }
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

// ---------------------------------------------------------------------------
// Release archives (AIS-312)
// ---------------------------------------------------------------------------

/**
 * Whether a release tag may be offered on `channel`: `stable` accepts only
 * vX.Y.Z, `preview` accepts stable and candidate tags, branch channels never
 * fit a tag.
 */
function tagFitsChannel(tag, channel) {
  const normalized = normalizeChannel(channel)
  if (normalized === 'stable') return isStableTag(tag)
  if (normalized === 'preview') return parseReleaseTag(tag) !== null
  return false
}

/** `https://github.com/<repo>/releases/download/<tag>/<asset>` */
function releaseDownloadUrl(tag, asset, repo = RELEASE_REPO) {
  return `https://github.com/${repo}/releases/download/${tag}/${asset}`
}

/**
 * Static URL of the latest *stable* manifest: GitHub redirects
 * `releases/latest/download/<asset>` to the newest non-draft, non-prerelease
 * release — exactly the stable channel — without touching the rate-limited
 * API.
 */
function latestManifestUrl(repo = RELEASE_REPO) {
  return `https://github.com/${repo}/releases/latest/download/${RELEASE_MANIFEST_ASSET}`
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0
}

/**
 * Validate a `hermes-release.json` for `channel`.
 *
 * Keep in sync with hermes_cli/release_update.py (validate_manifest) and
 * scripts/build_source_package.sh — the same manifest must pass or fail the
 * same way on both sides, otherwise the desktop offers what `hermes update`
 * then refuses (or vice versa). Unknown fields are ignored. `releaseTag` (the
 * GitHub release the manifest was downloaded from) must match the manifest's
 * `tag` when given.
 *
 * Returns `{ ok: true, manifest }` or `{ ok: false, error }` with a stable,
 * user-facing reason.
 */
function validateReleaseManifest(obj, { channel, releaseRepo = RELEASE_REPO, releaseTag = null } = {}) {
  const fail = error => ({ ok: false, error })
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return fail('manifest is not a JSON object')
  if (obj.format !== RELEASE_MANIFEST_FORMAT) return fail(`unsupported manifest format '${String(obj.format)}'`)

  const normalized = normalizeChannel(channel)
  for (const key of ['version', 'tag', 'commit_sha', 'source_archive', 'sha256', 'built_at']) {
    if (!isNonEmptyString(obj[key])) return fail(`manifest field '${key}' is missing or empty`)
  }
  const version = obj.version
  const tag = obj.tag
  const commitSha = obj.commit_sha
  const sourceArchive = obj.source_archive
  const sha256 = obj.sha256
  const builtAt = obj.built_at

  if (!parseReleaseTag(tag)) return fail(`tag '${tag}' is not a release tag (vX.Y.Z or vX.Y.Z-rc.N)`)
  if (releaseTag !== null && releaseTag !== undefined && tag !== releaseTag) {
    return fail(`manifest tag '${tag}' does not match release '${releaseTag}'`)
  }
  if (!tagFitsChannel(tag, normalized)) return fail(`tag '${tag}' is not a ${normalized} release`)
  if (version !== versionFromTag(tag)) return fail(`version '${version}' does not match tag '${tag}'`)
  if (!COMMIT_SHA_RE.test(commitSha)) return fail('commit_sha is not 40 lowercase hex characters')
  if (!SHA256_RE.test(sha256)) return fail('sha256 is not 64 lowercase hex characters')

  const size = obj.size
  if (typeof size !== 'number' || !Number.isInteger(size) || size <= 0) {
    return fail("manifest field 'size' is not a positive integer")
  }

  const expectedArchive = `hermes-source-${version}.zip`
  if (sourceArchive !== expectedArchive) {
    return fail(`source_archive '${sourceArchive}' is not the expected '${expectedArchive}'`)
  }

  if (Number.isNaN(Date.parse(builtAt))) return fail(`built_at '${builtAt}' is not an ISO 8601 timestamp`)

  const packageUrl = releaseDownloadUrl(tag, sourceArchive, releaseRepo)
  let parsedUrl
  try {
    parsedUrl = new URL(packageUrl)
  } catch {
    parsedUrl = null
  }
  if (!parsedUrl || parsedUrl.protocol !== 'https:' || parsedUrl.hostname.toLowerCase() !== 'github.com') {
    return fail('package URL is not an HTTPS github.com URL')
  }

  return {
    ok: true,
    manifest: {
      format: RELEASE_MANIFEST_FORMAT,
      version,
      tag,
      commit_sha: commitSha,
      source_archive: sourceArchive,
      sha256,
      size,
      built_at: builtAt,
      build_id: isNonEmptyString(obj.build_id) ? obj.build_id : builtAt,
      channel: normalized,
      package_url: packageUrl
    }
  }
}

/**
 * Read `<hermesRoot>/.hermes-release.json` (written by `hermes update` after
 * an archive update, hermes_cli/release_marker.py). `null` when absent,
 * unreadable or malformed — a corrupt marker must never wedge the updater,
 * the next archive update rewrites it. `readFile` is injectable for tests.
 */
function readReleaseMarker(hermesRoot, { readFile = fs.readFileSync } = {}) {
  let raw
  try {
    raw = readFile(path.join(String(hermesRoot || ''), RELEASE_MARKER_FILE), 'utf8')
  } catch {
    return null
  }
  let data
  try {
    data = JSON.parse(String(raw))
  } catch {
    return null
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null
  if (data.format !== RELEASE_MARKER_FORMAT) return null
  for (const key of ['channel', 'tag', 'version', 'commit_sha']) {
    if (!isNonEmptyString(data[key])) return null
  }
  const tag = data.tag.trim()
  const version = data.version.trim()
  const commitSha = data.commit_sha.trim()
  if (!parseReleaseTag(tag)) return null
  if (version !== versionFromTag(tag)) return null
  if (!COMMIT_SHA_RE.test(commitSha)) return null
  return {
    version,
    tag,
    commitSha,
    sha256: isNonEmptyString(data.sha256) ? data.sha256.trim() : '',
    buildId: isNonEmptyString(data.build_id) ? data.build_id.trim() : '',
    appliedAt: isNonEmptyString(data.applied_at) ? data.applied_at.trim() : '',
    channel: normalizeChannel(data.channel)
  }
}

/**
 * What a release-managed install reports against the channel's manifest.
 * The commit is authoritative (hermes_cli/release_update.py classify_feed):
 *
 *   - same commit                    → up to date (an rc promoted to stable
 *                                      on the same commit is *not* an update)
 *   - target tag newer than marker   → behind 1
 *   - marker tag newer than target   → behind 0, newerThanTarget (rc install
 *                                      on stable: never a downgrade, AIS-299)
 *   - same tag, different commit     → behind 1 (re-cut release; re-applying
 *                                      rewrites the marker instead of looping)
 */
function resolveReleaseStatus({ markerTag, markerCommit, targetTag, targetCommit }) {
  const headTag = String(markerTag || '')
  if (markerCommit && targetCommit && markerCommit === targetCommit) {
    return { behind: 0, newerThanTarget: false, headTag }
  }
  const cmp = compareReleaseTags(targetTag, markerTag)
  if (cmp < 0) {
    return { behind: 0, newerThanTarget: true, headTag }
  }
  return { behind: 1, newerThanTarget: false, headTag }
}

/**
 * Pick the release a `preview` (or `stable`) check targets from the GitHub
 * releases API list (`GET /repos/<repo>/releases`): drafts are skipped,
 * `stable` also skips prereleases and non-stable tags, the highest tag by
 * release order wins (a stable sorts above its own candidates). Returns
 * `{ tag, manifestUrl }` — the `browser_download_url` of the release's
 * `hermes-release.json` asset — or `null` when nothing fits or the chosen
 * release has no manifest asset.
 */
function selectReleaseFromApi(releases, channel) {
  const normalized = normalizeChannel(channel)
  if (!TAG_CHANNELS.includes(normalized)) return null
  let best = null
  for (const release of Array.isArray(releases) ? releases : []) {
    if (!release || typeof release !== 'object' || release.draft) continue
    const tag = String(release.tag_name || '').trim()
    if (!parseReleaseTag(tag)) continue
    if (normalized === 'stable' && (release.prerelease || !isStableTag(tag))) continue
    if (!best || compareReleaseTags(tag, best.tag) > 0) best = { tag, release }
  }
  if (!best) return null
  const assets = Array.isArray(best.release.assets) ? best.release.assets : []
  const asset = assets.find(a => a && typeof a === 'object' && String(a.name || '') === RELEASE_MANIFEST_ASSET)
  const manifestUrl = String(asset?.browser_download_url || '')
  if (!manifestUrl) return null
  return { tag: best.tag, manifestUrl }
}

module.exports = {
  RELEASE_MANIFEST_ASSET,
  RELEASE_MANIFEST_FORMAT,
  RELEASE_MARKER_FILE,
  RELEASE_MARKER_FORMAT,
  RELEASE_REPO,
  TAG_CHANNELS,
  compareReleaseTags,
  headReleaseTag,
  isStableTag,
  isTagChannel,
  latestManifestUrl,
  normalizeChannel,
  parseLsRemoteTags,
  parseReleaseTag,
  readReleaseMarker,
  releaseDownloadUrl,
  releaseSortKey,
  releaseTagIsNewer,
  resolveReleaseStatus,
  resolveTagChannelStatus,
  selectReleaseFromApi,
  selectReleaseTag,
  tagFitsChannel,
  validateReleaseManifest,
  versionFromTag
}
