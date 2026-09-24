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

describe('preprocessMarkdown raw URL autolinking', () => {
  it('leaves a markdown link whose label equals its href untouched (AIS-401)', () => {
    // The device-code login link the model writes. RAW_URL_RE permits `]`, `(`
    // and `)`, so the match used to run from the label through the href and the
    // wrapped result rendered as …/device%5D(…/device).
    const input = '[https://login.microsoft.com/device](https://login.microsoft.com/device)'

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('leaves a markdown link with differing label and href untouched', () => {
    const input = '[https://a.test/x](https://b.test/y)'

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('still autolinks a bare URL that merely follows a bracket', () => {
    const out = preprocessMarkdown('[see also] https://example.com/x')

    expect(out).toBe('[see also] <https://example.com/x>')
  })

  it('keeps parentheses that belong to the URL', () => {
    // Guarding on the preceding `[` rather than banning `()` from the pattern:
    // excluding them would truncate this to …/Foo_ .
    const out = preprocessMarkdown('siehe https://en.wikipedia.org/wiki/Foo_(bar) dort')

    expect(out).toBe('siehe <https://en.wikipedia.org/wiki/Foo_(bar)> dort')
  })

  it('does not double-wrap an existing autolink', () => {
    const input = 'schon <https://example.com/x> verlinkt'

    expect(preprocessMarkdown(input)).toBe(input)
  })
})
