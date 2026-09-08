import { describe, expect, it } from 'vitest'

import { FILE_PATH_RE, findBarePaths, isBareFilePath, isLinkablePathContext } from './paths'

const paths = (text: string) => findBarePaths(text).map(match => match.path)

describe('FILE_PATH_RE', () => {
  it('matches rooted, home, relative and Windows paths with an extension', () => {
    expect(paths('see /tmp/report.md and ~/notes/2026-09-08.md')).toEqual(['/tmp/report.md', '~/notes/2026-09-08.md'])
    expect(paths('rel ./out/index.html or ../x/y.pdf')).toEqual(['./out/index.html', '../x/y.pdf'])
    expect(paths('win C:\\Users\\me\\brief.md done')).toEqual(['C:\\Users\\me\\brief.md'])
    expect(paths('(/tmp/a.md) "/tmp/b.md"')).toEqual(['/tmp/a.md', '/tmp/b.md'])
  })

  it('requires a file extension so URL-ish paths and directories stay plain', () => {
    expect(paths('GET /api/cron/jobs returns rows')).toEqual([])
    expect(paths('cd /tmp/output and look')).toEqual([])
    expect(paths('/api/cron/jobs/42/output/latest')).toEqual([])
  })

  it('peels trailing punctuation', () => {
    expect(paths('Saved to /tmp/report.md.')).toEqual(['/tmp/report.md'])
    expect(paths('Is it /tmp/report.md?')).toEqual(['/tmp/report.md'])
    expect(paths('Files: /tmp/a.md, /tmp/b.md; done')).toEqual(['/tmp/a.md', '/tmp/b.md'])
  })

  it('resets lastIndex between callers (global regex)', () => {
    expect('/tmp/a.md'.match(FILE_PATH_RE)).toHaveLength(1)
    expect('/tmp/a.md'.match(FILE_PATH_RE)).toHaveLength(1)
  })
})

describe('isLinkablePathContext', () => {
  it('skips existing link targets, autolinks and URL paths', () => {
    expect(paths('[open](/tmp/a.md)')).toEqual([])
    expect(paths('</tmp/a.md>')).toEqual([])
    expect(paths('https://example.com/docs/a.md')).toEqual([])
    expect(paths('file:///tmp/a.md')).toEqual([])
    expect(isLinkablePathContext('x ](/tmp/a.md', 4)).toBe(false)
    expect(isLinkablePathContext('see /tmp/a.md', 4)).toBe(true)
  })
})

describe('isBareFilePath', () => {
  it('accepts a single path and rejects prose', () => {
    expect(isBareFilePath('~/notes/today.md')).toBe(true)
    expect(isBareFilePath(' /tmp/report.pdf ')).toBe(true)
    expect(isBareFilePath('C:\\work\\a.docx')).toBe(true)
    expect(isBareFilePath('open /tmp/report.pdf now')).toBe(false)
    expect(isBareFilePath('/tmp/dir')).toBe(false)
    expect(isBareFilePath('npm run build')).toBe(false)
  })
})
