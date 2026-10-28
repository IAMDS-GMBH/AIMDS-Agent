/**
 * Tiny changelog builder. Takes a list of raw commit summaries, parses the
 * Conventional Commits 1.0 header (`type(scope)!: subject`), filters internal
 * noise (chore/ci/docs/tests, test-only commits), scrubs ticket/PR numbers and
 * trailing technical clauses, and groups the rest into buckets (What's new,
 * Fixed, Faster, Improved).
 *
 * Since AIS-750 this is only the fallback: release channels show the curated
 * notes (`release-notes/<X.Y.Z>.json`, see lib/update-notes.ts); the commit
 * list is the developer view of the main channel.
 *
 * Inlined (rather than depending on `conventional-commits-parser`) because
 * that package's index re-exports a Node `stream` helper which won't load
 * in the sandboxed Electron renderer, and its actual parse logic for the
 * header is a small regex.
 */

export type CommitGroupId = 'new' | 'fixed' | 'faster' | 'improved' | 'other'

export interface CommitGroup {
  id: CommitGroupId
  label: string
  items: string[]
}

export interface ParsedCommit {
  type: null | string
  scope: null | string
  breaking: boolean
  subject: string
}

export interface CommitChangelogInput {
  summary?: string
  /** Every changed path is a test file (main process, `git log --name-only`). */
  testsOnly?: boolean
}

export interface BuildOptions {
  maxGroups?: number
  maxPerGroup?: number
  maxTotal?: number
  locale?: string
}

const GROUP_META: Record<CommitGroupId, { label: Record<string, string>; order: number }> = {
  new: { label: { en: "What's new", de: "Neuerungen" }, order: 0 },
  fixed: { label: { en: 'Fixed', de: 'Fehlerbehebungen' }, order: 1 },
  faster: { label: { en: 'Faster', de: 'Performance' }, order: 2 },
  improved: { label: { en: 'Improved', de: 'Verbesserungen' }, order: 3 },
  other: { label: { en: 'Other improvements', de: 'Weitere Änderungen' }, order: 4 }
}

const TYPE_TO_GROUP: Record<string, CommitGroupId> = {
  feat: 'new',
  feature: 'new',
  fix: 'fixed',
  bugfix: 'fixed',
  hotfix: 'fixed',
  revert: 'fixed',
  perf: 'faster',
  performance: 'faster',
  refactor: 'improved',
  style: 'improved',
  a11y: 'improved',
  ui: 'improved',
  ux: 'improved'
}

const HIDDEN_TYPES = new Set([
  'build',
  'chore',
  'ci',
  'dep',
  'deps',
  'doc',
  'docs',
  'lint',
  'release',
  'test',
  'tests',
  'wip'
])

// Scopes and subjects that say "tests only" — nothing a user can notice.
const HIDDEN_SCOPES = new Set(['test', 'tests', 'ci', 'e2e', 'build', 'deps'])
const TESTS_ONLY_SUBJECT = /^(?:the\s+)?(?:tests?|test runs?|test suite|unit tests?|test fixtures?|ci)\b|\b(?:tests? only|only (?:in )?tests?)\b/i

/** The single line shown when nothing user-facing is left (AIS-750). */
export function getFallbackGroup(locale: string): CommitGroup {
  const isDe = locale.startsWith('de')

  return {
    id: 'other',
    items: [isDe ? 'Fehlerbehebungen und Verbesserungen' : 'Fixes and improvements'],
    label: isDe ? 'In diesem Update' : 'In this update'
  }
}

/** Localized label of a changelog / release-notes group. */
export function changelogGroupLabel(id: CommitGroupId, locale: string): string {
  const lang = locale.startsWith('de') ? 'de' : 'en'

  return GROUP_META[id].label[lang] ?? GROUP_META[id].label.en
}

/** Display order of a group (new → fixed → faster → improved → other). */
export function changelogGroupOrder(id: CommitGroupId): number {
  return GROUP_META[id].order
}

const CONVENTIONAL_HEADER = /^(?<type>[a-zA-Z][a-zA-Z0-9_-]*)(?:\((?<scope>[^)]+)\))?(?<bang>!)?:\s+(?<subject>.+)$/

/** Infer a type for unclassified commits using common English/German keywords */
function inferCommitType(subject: string): string | null {
  const s = subject.toLowerCase()

  if (/\b(fix|fixed|fixes|bug|bugfix|hotfix|korrektur|behoben|revert)\b/.test(s)) {return 'fix'}

  if (/\b(add|adds|added|feat|feature|new|neu|erstellt|unterstützung)\b/.test(s)) {return 'feat'}

  if (/\b(perf|performance|faster|speed|schneller|beschleunigt)\b/.test(s)) {return 'perf'}

  if (/\b(improve|improved|optimization|optimize|refactor|style|styling|ui|ux|redesign|layout|design|verbessert|optimiert)\b/.test(s)) {return 'refactor'}

  return null
}

/** Parse a single commit header line per Conventional Commits 1.0. */
export function parseCommitHeader(raw: string): ParsedCommit {
  const header = (raw ?? '').split(/\r?\n/, 1)[0].trim()

  if (!header) {
    return { breaking: false, scope: null, subject: '', type: null }
  }

  const match = CONVENTIONAL_HEADER.exec(header)

  if (!match?.groups) {
    return { breaking: false, scope: null, subject: header, type: null }
  }

  return {
    breaking: Boolean(match.groups.bang),
    scope: match.groups.scope ?? null,
    subject: match.groups.subject.trim(),
    type: match.groups.type.toLowerCase()
  }
}

// Ticket keys of the trackers this repository uses (AIS-750): AIS-123,
// SUP-20261027-091112, DEVOPS-101. Explicit prefixes, so UTF-8 or SHA-256
// survive.
const TICKET_KEY = /\[?\b(?:AIS|SUP|DEVOPS)-\d+(?:-\d+)*\b\]?:?/g

/**
 * Strip what only developers read: `(#233)` PR references, ticket keys, and
 * the technical clause after a dash or semicolon ("Adopt X — one call per
 * set, …" → "Adopt X").
 */
export function scrubSubject(subject: string): string {
  return subject
    .replace(/\s*\(#\d+\)/g, '')
    .replace(/(^|\s)#\d+\b/g, '$1')
    .replace(TICKET_KEY, '')
    .replace(/\(\s*[,;&/]*\s*\)/g, '')
    .split(/\s+[—–]\s+|\s+--?\s+|\s*;\s+/, 1)[0]
    .replace(/\s*\([^()]*\)\s*$/, '')
    .replace(/^[\s:,;–—-]+/, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** True for commits a user cannot notice: tests/CI only, by files or by message. */
export function isInternalCommit(commit: CommitChangelogInput, parsed: ParsedCommit = parseCommitHeader(commit.summary ?? '')): boolean {
  if (commit.testsOnly) {
    return true
  }

  if (parsed.type && HIDDEN_TYPES.has(parsed.type)) {
    return true
  }

  const scopes = (parsed.scope ?? '').split(/[\s,]+/).map(scope => scope.toLowerCase())

  if (scopes.some(scope => HIDDEN_SCOPES.has(scope))) {
    return true
  }

  return TESTS_ONLY_SUBJECT.test(scrubSubject(parsed.subject))
}

function tidySubject(subject: string): string {
  const cleaned = scrubSubject(subject)
    .replace(/\s+/g, ' ')
    .replace(/[.;,\s]+$/, '')
    .trim()

  if (!cleaned) {
    return cleaned
  }

  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1)
}

/**
 * Build a small grouped changelog from a list of raw commits.
 * Always returns at least one group; falls back to a neutral placeholder
 * when every commit was filtered or unparseable.
 */
export function buildCommitChangelog(
  commits: readonly CommitChangelogInput[] | undefined,
  options: BuildOptions = {}
): CommitGroup[] {
  const { maxGroups = 3, maxPerGroup = 4, maxTotal = 6, locale = 'en' } = options
  const lang = locale.startsWith('de') ? 'de' : 'en'
  const groups = new Map<CommitGroupId, string[]>()
  const seen = new Set<string>()
  let total = 0

  for (const commit of commits ?? []) {
    if (total >= maxTotal) {
      break
    }

    const parsed = parseCommitHeader(commit.summary ?? '')

    if (isInternalCommit(commit, parsed)) {
      continue
    }

    const effectiveType = parsed.type ?? inferCommitType(parsed.subject)
    const groupId: CommitGroupId = effectiveType ? (TYPE_TO_GROUP[effectiveType] ?? 'other') : 'other'
    const subject = tidySubject(parsed.subject)

    if (!subject) {
      continue
    }

    const dedupeKey = subject.toLowerCase()

    if (seen.has(dedupeKey)) {
      continue
    }

    const bucket = groups.get(groupId) ?? []

    if (bucket.length >= maxPerGroup) {
      continue
    }

    bucket.push(subject)
    groups.set(groupId, bucket)
    seen.add(dedupeKey)
    total += 1
  }

  const result = Array.from(groups.entries())
    .map(([id, items]) => ({
      id,
      items,
      label: GROUP_META[id].label[lang] ?? GROUP_META[id].label.en,
      order: GROUP_META[id].order
    }))
    .sort((a, b) => a.order - b.order)
    .slice(0, maxGroups)
    .map(({ id, items, label }): CommitGroup => ({ id, items, label }))

  if (result.length === 0) {
    return [getFallbackGroup(lang)]
  }

  return result
}
