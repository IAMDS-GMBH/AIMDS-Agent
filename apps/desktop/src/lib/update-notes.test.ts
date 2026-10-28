import { describe, expect, it } from 'vitest'

import type { DesktopReleaseNote, DesktopUpdateStatus } from '@/global'
import { de } from '@/i18n/de'
import { en } from '@/i18n/en'

import { buildReleaseNoteGroups, formatVersionLabel, isReleaseUpdate, resolveUpdateHeader, resolveUpdateNotes } from './update-notes'

// AIS-750: what Johannes saw on v0.7.13-rc.1 → the header and list now.
const previewStatus: DesktopUpdateStatus = {
  behind: 12,
  branch: 'preview',
  installedVersion: '0.7.12',
  source: 'git',
  supported: true,
  targetTag: 'v0.7.13-rc.3',
  targetVersion: '0.7.13-rc.3'
}

const notes: DesktopReleaseNote[] = [
  { de: 'Kanäle in Microsoft Teams lesen und dorthin posten', en: 'Read Microsoft Teams channels and post to them', group: 'new' },
  { de: 'Wartende Freigaben stehen jetzt sichtbar am Ende der Antwort', en: 'Pending approvals now show at the end of the answer', group: 'fixed' },
  { de: 'Lange Unterhaltungen bleiben flüssig', en: 'Long conversations stay responsive', group: 'improved' },
  { de: 'Nur auf Deutsch', en: '', group: 'fixed' },
  { de: '', en: 'English only', group: 'fixed' }
]

const rcCommits = [
  { at: 0, author: 'dev', sha: 'a', summary: 'feat(AIS-711): adopt OpenProject MCP 0.6 — one call per set (#233)' },
  { at: 0, author: 'dev', sha: 'b', summary: 'fix(AIS-728): test runs no longer write into the real ~/.hermes/logs (#232)', testsOnly: true }
]

describe('update header', () => {
  it('shows the target version, a candidate as preview, no branch and no rc number', () => {
    expect(resolveUpdateHeader(previewStatus, 12, de.updates)).toEqual({
      developer: false,
      installedLine: 'Installiert: Version 0.7.12',
      versionLine: 'Version 0.7.13 – Vorabversion'
    })
    expect(resolveUpdateHeader(previewStatus, 12, en.updates).versionLine).toBe('Version 0.7.13 – preview')
  })

  it('shows a stable release as a plain version', () => {
    const stable = { ...previewStatus, branch: 'stable', installedVersion: '0.7.13', targetTag: 'v0.7.14', targetVersion: '0.7.14' }

    expect(resolveUpdateHeader(stable, 3, de.updates).versionLine).toBe('Version 0.7.14')
    expect(resolveUpdateHeader(stable, 3, en.updates)).toEqual({
      developer: false,
      installedLine: 'Installed: Version 0.7.13',
      versionLine: 'Version 0.7.14'
    })
    // legacy alias `tags` and a release-managed install are release channels too
    expect(isReleaseUpdate({ ...stable, branch: 'tags' })).toBe(true)
    expect(isReleaseUpdate({ branch: 'main', source: 'release', supported: true })).toBe(true)
  })

  it('drops the installed line when it would repeat the target or show a dev number', () => {
    expect(resolveUpdateHeader({ ...previewStatus, installedVersion: '0.7.13-rc.1' }, 2, de.updates).installedLine).toBeNull()
    expect(resolveUpdateHeader({ ...previewStatus, installedVersion: '0.7.12+6' }, 2, de.updates).installedLine).toBeNull()
    expect(resolveUpdateHeader({ ...previewStatus, installedVersion: undefined }, 2, de.updates).installedLine).toBeNull()
  })

  it('labels the main channel as a development build with the commit count', () => {
    const main: DesktopUpdateStatus = { behind: 12, branch: 'main', source: 'git', supported: true }

    expect(resolveUpdateHeader(main, 12, de.updates)).toEqual({ developer: true, installedLine: null, versionLine: 'Entwicklungsstand · +12 Änderungen' })
    expect(resolveUpdateHeader(main, 1, en.updates).versionLine).toBe('development build · +1 commit')
    // remote backend status: no channel at all
    expect(resolveUpdateHeader({ behind: 4, supported: true }, 4, en.updates).versionLine).toBe('+4 commits')
  })

  it('formats versions and keeps an unknown string only when nothing else exists', () => {
    expect(formatVersionLabel('v0.7.13-rc.3', de.updates)).toBe('Version 0.7.13 – Vorabversion')
    expect(formatVersionLabel('0.7.14', en.updates)).toBe('Version 0.7.14')
    expect(formatVersionLabel('2026.10-hotfix', en.updates)).toBe('Version 2026.10-hotfix')
    expect(formatVersionLabel('', en.updates)).toBe('')
  })
})

describe('update notes', () => {
  it('groups curated notes new → improved → fixed in exactly one language, falling back per entry', () => {
    expect(buildReleaseNoteGroups(notes, 'de')).toEqual([
      { id: 'new', items: ['Kanäle in Microsoft Teams lesen und dorthin posten'], label: 'Neuerungen' },
      { id: 'improved', items: ['Lange Unterhaltungen bleiben flüssig'], label: 'Verbesserungen' },
      {
        id: 'fixed',
        items: ['Wartende Freigaben stehen jetzt sichtbar am Ende der Antwort', 'Nur auf Deutsch', 'English only'],
        label: 'Fehlerbehebungen'
      }
    ])
    expect(buildReleaseNoteGroups(notes, 'en')[2].items).toEqual(['Pending approvals now show at the end of the answer', 'Nur auf Deutsch', 'English only'])
  })

  it('prefers curated notes over commits', () => {
    const result = resolveUpdateNotes({ commits: rcCommits, locale: 'de', status: { ...previewStatus, releaseNotes: notes } })

    expect(result.curated).toBe(true)
    expect(JSON.stringify(result.groups)).not.toMatch(/OpenProject MCP|#\d|AIS-/)
  })

  it('shows one neutral line on a release channel without notes — never commit subjects', () => {
    const result = resolveUpdateNotes({ commits: rcCommits, locale: 'de', status: { ...previewStatus, releaseNotes: [] } })

    expect(result).toEqual({
      commitItems: 0,
      curated: false,
      groups: [{ id: 'other', items: ['Fehlerbehebungen und Verbesserungen'], label: 'In diesem Update' }]
    })
    expect(resolveUpdateNotes({ commits: rcCommits, locale: 'en', status: previewStatus }).groups[0].items).toEqual(['Fixes and improvements'])
  })

  it('keeps the scrubbed commit subjects as the main-channel developer view', () => {
    const result = resolveUpdateNotes({ commits: rcCommits, locale: 'de', status: { behind: 2, branch: 'main', supported: true } })

    expect(result.curated).toBe(false)
    expect(result.commitItems).toBe(1)
    expect(result.groups).toEqual([{ id: 'new', items: ['Adopt OpenProject MCP 0.6'], label: 'Neuerungen' }])
  })
})
