import { describe, expect, it } from 'vitest'

import { buildCommitChangelog, isInternalCommit, parseCommitHeader, scrubSubject } from './commit-changelog'

describe('parseCommitHeader', () => {
  it('extracts type, scope, and subject from a conventional header', () => {
    expect(parseCommitHeader('feat(desktop): NSIS prereq detection page')).toEqual({
      breaking: false,
      scope: 'desktop',
      subject: 'NSIS prereq detection page',
      type: 'feat'
    })
  })

  it('flags breaking changes via the `!` marker', () => {
    expect(parseCommitHeader('feat(api)!: change endpoint shape')).toMatchObject({
      breaking: true,
      type: 'feat'
    })
  })

  it('treats non-conventional commits as untyped with the full header as subject', () => {
    expect(parseCommitHeader('Update README')).toEqual({
      breaking: false,
      scope: null,
      subject: 'Update README',
      type: null
    })
  })

  it('ignores body lines and trims whitespace', () => {
    expect(parseCommitHeader('  fix: handle null input  \n\nMore detail')).toMatchObject({
      subject: 'handle null input',
      type: 'fix'
    })
  })

  it('returns empty subject for blank input', () => {
    expect(parseCommitHeader('')).toEqual({ breaking: false, scope: null, subject: '', type: null })
  })
})

describe('buildCommitChangelog', () => {
  it('groups commits into user-friendly buckets and capitalizes subjects', () => {
    const groups = buildCommitChangelog([
      { summary: 'feat(desktop): add NSIS prereq detection page' },
      { summary: 'fix(sidebar): jitter when dragging' },
      { summary: 'perf: shave 200ms off cold start' },
      { summary: 'refactor: extract sidebar row component' }
    ])

    expect(groups.map(g => g.id)).toEqual(['new', 'fixed', 'faster'])
    expect(groups[0]).toMatchObject({ label: "What's new" })
    expect(groups[0].items[0]).toBe('Add NSIS prereq detection page')
    expect(groups[1].items[0]).toBe('Jitter when dragging')
  })

  it('hides chore/ci/docs/test commits', () => {
    const groups = buildCommitChangelog([
      { summary: 'chore: bump deps' },
      { summary: 'ci: tweak workflow' },
      { summary: 'docs: spelling fix' },
      { summary: 'feat: real new feature' }
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].items).toEqual(['Real new feature'])
  })

  it('includes style commits under improved group', () => {
    const groups = buildCommitChangelog([
      { summary: 'style(desktop): remove gateway connection header block' },
      { summary: 'feat(agent): add data verification workflow' }
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].id).toBe('new')
    expect(groups[1].id).toBe('improved')
    expect(groups[1].items[0]).toBe('Remove gateway connection header block')
  })

  it('routes unparseable commits to the "Other improvements" bucket or infers type from keywords', () => {
    const groups = buildCommitChangelog([{ summary: 'Miscellaneous update' }, { summary: 'Windows installer fix for powershell' }])

    expect(groups[0].id).toBe('fixed')
    expect(groups[0].items).toEqual(['Windows installer fix for powershell'])
    expect(groups[1].id).toBe('other')
    expect(groups[1].items).toEqual(['Miscellaneous update'])
  })

  it('falls back to a neutral placeholder when every commit is filtered or empty', () => {
    const groups = buildCommitChangelog([{ summary: 'chore: bump' }, { summary: 'ci: stuff' }])

    expect(groups).toEqual([{ id: 'other', items: ['Fixes and improvements'], label: 'In this update' }])
    expect(buildCommitChangelog([], { locale: 'de' })).toEqual([
      { id: 'other', items: ['Fehlerbehebungen und Verbesserungen'], label: 'In diesem Update' }
    ])
  })

  it('provides German localized group labels and fallbacks', () => {
    const groups = buildCommitChangelog([{ summary: 'fix: null reference' }], { locale: 'de' })

    expect(groups[0].label).toBe('Fehlerbehebungen')
  })

  it('dedupes identical subjects and caps the items per group', () => {
    const groups = buildCommitChangelog(
      [
        { summary: 'fix: thing A' },
        { summary: 'fix: thing A' },
        { summary: 'fix: thing B' },
        { summary: 'fix: thing C' },
        { summary: 'fix: thing D' },
        { summary: 'fix: thing E' }
      ],
      { maxPerGroup: 3, maxTotal: 10 }
    )

    expect(groups[0].items).toEqual(['Thing A', 'Thing B', 'Thing C'])
  })

  it('caps total entries across buckets', () => {
    const groups = buildCommitChangelog(
      [
        { summary: 'feat: a' },
        { summary: 'feat: b' },
        { summary: 'fix: c' },
        { summary: 'fix: d' },
        { summary: 'perf: e' }
      ],
      { maxTotal: 3 }
    )

    const totalItems = groups.reduce((sum, g) => sum + g.items.length, 0)
    expect(totalItems).toBe(3)
  })
})

// AIS-750: the subjects the v0.7.13-rc.1 dialog showed verbatim.
describe('scrubSubject', () => {
  it('strips PR numbers, ticket keys and the technical clause after a dash', () => {
    expect(scrubSubject('adopt OpenProject MCP 0.6 — one call per set, no per-project loops, local server on the 0.6.1 contract (#233)')).toBe(
      'adopt OpenProject MCP 0.6'
    )
    expect(scrubSubject('chat history survives a running turn — per-round persistence, resume re-binds to the live session (#234)')).toBe(
      'chat history survives a running turn'
    )
    expect(scrubSubject('AIS-735: login works again (SUP-20261027-091112)')).toBe('login works again')
    expect(scrubSubject('[AIS-12] fix crash; see issue #38727')).toBe('fix crash')
    expect(scrubSubject('read UTF-8 and SHA-256 files')).toBe('read UTF-8 and SHA-256 files')
  })
})

describe('internal commits', () => {
  it('hides test/ci/build/chore types, test scopes, test-only subjects and test-only file sets', () => {
    expect(isInternalCommit({ summary: 'test: cover the parser' })).toBe(true)
    expect(isInternalCommit({ summary: 'build(desktop): bump electron' })).toBe(true)
    expect(isInternalCommit({ summary: 'fix(tests): flaky gateway fixture' })).toBe(true)
    expect(
      isInternalCommit({ summary: 'fix(AIS-728): test runs no longer write into the real ~/.hermes/logs, so test noise stays out of support bundles (#232)' })
    ).toBe(true)
    expect(isInternalCommit({ summary: 'fix: something users notice', testsOnly: true })).toBe(true)
    expect(isInternalCommit({ summary: 'fix(AIS-749): the desktop reconnects after a nightly restart (#235)' })).toBe(false)
    expect(isInternalCommit({ summary: 'fix: the latest model list loads again' })).toBe(false)
  })

  it('builds the developer list from the rc.1 commits without numbers or test-only changes', () => {
    const groups = buildCommitChangelog(
      [
        { summary: 'feat(AIS-711): adopt OpenProject MCP 0.6 — one call per set, no per-project loops (#233)' },
        { summary: 'fix(AIS-728): test runs no longer write into the real ~/.hermes/logs (#232)', testsOnly: true },
        { summary: 'fix(AIS-735, AIS-736): chat history survives a running turn — per-round persistence (#234)' }
      ],
      { locale: 'de' }
    )

    expect(groups).toEqual([
      { id: 'new', items: ['Adopt OpenProject MCP 0.6'], label: 'Neuerungen' },
      { id: 'fixed', items: ['Chat history survives a running turn'], label: 'Fehlerbehebungen' }
    ])
    expect(JSON.stringify(groups)).not.toMatch(/#\d|AIS-|test runs/)
  })

  it('shows one generic line when only internal commits remain', () => {
    const groups = buildCommitChangelog([{ summary: 'fix(AIS-728): test runs stay hermetic (#232)' }, { summary: 'ci: cache' }], { locale: 'de' })

    expect(groups).toEqual([{ id: 'other', items: ['Fehlerbehebungen und Verbesserungen'], label: 'In diesem Update' }])
  })
})
