/**
 * What the updates overlay says about an update (AIS-750): the version line
 * in the header and the list of changes.
 *
 * Release channels (stable / preview, and every release-managed install)
 * talk to end users: the target version ("Version 0.7.13 – preview") and the
 * curated notes the main process read from `release-notes/<X.Y.Z>.json`.
 * Without notes they show one neutral line — never developer commit
 * subjects. The `main` channel is the developer view: "development build",
 * the commit count and the scrubbed commit subjects.
 *
 * Pure, so the copy is unit-testable without rendering the dialog.
 */
import type { DesktopReleaseNote, DesktopUpdateCommit, DesktopUpdateStatus } from '@/global'

import { buildCommitChangelog, changelogGroupLabel, type CommitGroup, type CommitGroupId, getFallbackGroup } from './commit-changelog'

export interface UpdateHeaderCopy {
  /** "Version 0.7.14" */
  versionLabel: (version: string) => string
  /** "Version 0.7.13 – preview" */
  previewVersionLabel: (version: string) => string
  /** "Installed: Version 0.7.12" — gets the formatted version label. */
  installedVersion: (label: string) => string
  /** "development build" */
  devBuild: string
  /** "+12 commits" */
  changeCount: (count: number) => string
}

export interface UpdateHeader {
  /** Release channels: the target version; main: "development build · +N". */
  versionLine: string
  /** The installed version, when it is known and tells the user something. */
  installedLine: null | string
  /** Developer view (main channel / unknown channel): commit subjects allowed. */
  developer: boolean
}

const CHANNEL_ALIASES: Record<string, string> = { beta: 'preview', rc: 'preview', release: 'stable', tags: 'stable' }
const VERSION_RE = /^v?(\d+\.\d+\.\d+)(-rc\.\d+)?(?:\+\d+)?$/
const NOTE_GROUP_ORDER: CommitGroupId[] = ['new', 'improved', 'fixed']

function normalizeChannel(branch: string | undefined): string {
  const value = (branch ?? '').trim().toLowerCase()

  return CHANNEL_ALIASES[value] ?? value
}

/** stable / preview / release-managed install → the end-user view. */
export function isReleaseUpdate(status: DesktopUpdateStatus | null | undefined): boolean {
  if (!status) {
    return false
  }

  if (status.source === 'release') {
    return true
  }

  const channel = normalizeChannel(status.branch)

  return channel === 'stable' || channel === 'preview'
}

function parseVersion(raw: string | undefined): null | { base: string; candidate: boolean; dev: boolean } {
  const value = (raw ?? '').trim()
  const match = VERSION_RE.exec(value)

  if (!match) {
    return null
  }

  return { base: match[1], candidate: Boolean(match[2]), dev: value.includes('+') }
}

/**
 * "Version 0.7.13 – preview" for a candidate, "Version 0.7.14" for a release.
 * The candidate number (-rc.3) only shows when nothing else is known.
 */
export function formatVersionLabel(raw: string | undefined, copy: UpdateHeaderCopy): string {
  const parsed = parseVersion(raw)

  if (parsed) {
    return parsed.candidate ? copy.previewVersionLabel(parsed.base) : copy.versionLabel(parsed.base)
  }

  const value = (raw ?? '').trim().replace(/^v(?=\d)/, '')

  return value ? copy.versionLabel(value) : ''
}

export function resolveUpdateHeader(status: DesktopUpdateStatus | null | undefined, behind: number, copy: UpdateHeaderCopy): UpdateHeader {
  if (!isReleaseUpdate(status)) {
    const channel = normalizeChannel(status?.branch)
    const count = behind > 0 ? copy.changeCount(behind) : ''
    // A backend status carries no channel: the count is all there is.
    const parts = channel ? [copy.devBuild, count] : [count]

    return { developer: true, installedLine: null, versionLine: parts.filter(Boolean).join(' · ') }
  }

  const target = status?.targetVersion || status?.targetTag || ''
  const versionLine = formatVersionLabel(target, copy) || copy.changeCount(Math.max(behind, 1))
  const installed = parseVersion(status?.installedVersion || status?.releaseVersion)
  const targetParsed = parseVersion(target)

  // Same base version (0.7.13-rc.1 → -rc.3) or a dev checkout: the line would
  // only repeat the target or show a developer number.
  const installedLine =
    installed && !installed.dev && targetParsed && installed.base !== targetParsed.base
      ? copy.installedVersion(formatVersionLabel(status?.installedVersion || status?.releaseVersion, copy))
      : null

  return { developer: false, installedLine, versionLine }
}

/** Curated notes → groups (new, improved, fixed) in the user's language. */
export function buildReleaseNoteGroups(notes: readonly DesktopReleaseNote[] | undefined, locale: string): CommitGroup[] {
  const lang = locale.startsWith('de') ? 'de' : 'en'
  const other = lang === 'de' ? 'en' : 'de'
  const buckets = new Map<CommitGroupId, string[]>()

  for (const note of notes ?? []) {
    // Exactly one language per dialog: the UI's, else the entry's other one.
    const text = (note[lang] || note[other] || '').trim()

    if (!text || !NOTE_GROUP_ORDER.includes(note.group)) {
      continue
    }

    const bucket = buckets.get(note.group) ?? []

    if (!bucket.includes(text)) {
      bucket.push(text)
    }

    buckets.set(note.group, bucket)
  }

  return NOTE_GROUP_ORDER.filter(id => buckets.has(id)).map(id => ({
    id,
    items: buckets.get(id) ?? [],
    label: changelogGroupLabel(id, lang)
  }))
}

export interface UpdateNotes {
  groups: CommitGroup[]
  /** Curated notes are shown (no "+N more changes" footer then). */
  curated: boolean
  /** Number of list rows that stand for real commits (developer view). */
  commitItems: number
}

export function resolveUpdateNotes({
  commits,
  locale,
  status
}: {
  commits: readonly DesktopUpdateCommit[]
  locale: string
  status: DesktopUpdateStatus | null | undefined
}): UpdateNotes {
  const curated = buildReleaseNoteGroups(status?.releaseNotes, locale)

  if (curated.length) {
    return { commitItems: 0, curated: true, groups: curated }
  }

  if (isReleaseUpdate(status)) {
    return { commitItems: 0, curated: false, groups: [getFallbackGroup(locale)] }
  }

  const groups = buildCommitChangelog(commits, { locale })
  const generic = groups.length === 1 && groups[0].id === 'other' && groups[0].items[0] === getFallbackGroup(locale).items[0]

  return {
    commitItems: generic ? 0 : groups.reduce((sum, group) => sum + group.items.length, 0),
    curated: false,
    groups
  }
}

export interface UpdateIndicatorCopy extends UpdateHeaderCopy {
  /** "+ 3 more changes included." */
  moreChanges: (count: number) => string
}

export interface ReleaseUpdateIndicator {
  /** Compact target for the status-bar pill: "v0.7.14" (never the rc number). */
  label: string
  /** "Version 0.7.14 – preview", like the dialog header. */
  heading: string
  installedLine: null | string
  /** The dialog's groups, cut to `maxItems` rows in total. */
  groups: CommitGroup[]
  /** "+ N more changes included." when rows were cut, else null. */
  more: null | string
}

/**
 * AIS-755: the status-bar version pill of a release channel says what the
 * update dialog says — the target version and the curated notes in the UI
 * language — instead of the branch and a commit count. Same data as the
 * dialog (the last check's `releaseNotes`), no extra fetch. Null for the
 * developer view (main channel) and when nothing is pending; the caller keeps
 * its commit-count hint there.
 */
export function resolveReleaseUpdateIndicator({
  copy,
  locale,
  maxItems = 6,
  status
}: {
  copy: UpdateIndicatorCopy
  locale: string
  maxItems?: number
  status: DesktopUpdateStatus | null | undefined
}): null | ReleaseUpdateIndicator {
  const behind = status?.behind ?? 0

  if (!status || behind <= 0 || !isReleaseUpdate(status)) {
    return null
  }

  const header = resolveUpdateHeader(status, behind, copy)
  const target = (status.targetVersion || status.targetTag || '').trim()
  const parsed = parseVersion(target)
  const label = parsed ? `v${parsed.base}` : target ? `v${target.replace(/^v(?=\d)/, '')}` : header.versionLine
  const { groups } = resolveUpdateNotes({ commits: [], locale, status })

  let left = Math.max(1, maxItems)
  let hidden = 0
  const shown: CommitGroup[] = []

  for (const group of groups) {
    const items = group.items.slice(0, left)
    hidden += group.items.length - items.length
    left -= items.length

    if (items.length) {
      shown.push({ ...group, items })
    }
  }

  return {
    groups: shown,
    heading: header.versionLine,
    installedLine: header.installedLine,
    label,
    more: hidden > 0 ? copy.moreChanges(hidden) : null
  }
}
