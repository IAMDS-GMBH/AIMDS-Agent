import { describe, expect, it } from 'vitest'

import { autoLinkBarePaths, preprocessMarkdown } from './markdown-preprocess'

describe('autoLinkBarePaths', () => {
  it('links bare paths in prose', () => {
    expect(autoLinkBarePaths('Saved to /tmp/report.md.')).toBe('Saved to [/tmp/report.md](#path/%2Ftmp%2Freport.md).')
    expect(autoLinkBarePaths('see ~/notes/a.md')).toBe('see [~/notes/a.md](#path/~%2Fnotes%2Fa.md)')
  })

  it('leaves existing links, autolinks and URLs alone', () => {
    const text = '[open](/tmp/a.md) </tmp/b.md> https://x.test/c.md [/tmp/d.md](file:///tmp/d.md)'

    expect(autoLinkBarePaths(text)).toBe(text)
  })

  it('returns the input untouched when nothing matches', () => {
    expect(autoLinkBarePaths('no paths here')).toBe('no paths here')
  })
})

describe('preprocessMarkdown path links', () => {
  it('links paths in prose but not inside code spans or fences', () => {
    const input = ['Wrote /tmp/out.md and `~/x.md`.', '', '```sh', 'cat /tmp/out.md', '```'].join('\n')
    const out = preprocessMarkdown(input)

    expect(out).toContain('[/tmp/out.md](#path/%2Ftmp%2Fout.md)')
    expect(out).toContain('`~/x.md`')
    expect(out).toContain('cat /tmp/out.md\n')
    expect(out).not.toContain('[cat')
  })

  it('does not link paths inside raw URLs', () => {
    const out = preprocessMarkdown('Docs: https://example.com/docs/guide.md')

    expect(out).toBe('Docs: <https://example.com/docs/guide.md>')
  })
})
