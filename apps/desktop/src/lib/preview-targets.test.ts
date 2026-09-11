import { describe, expect, it } from 'vitest'

import {
  extractPreviewTargets,
  pathFromMarkdownHref,
  pathMarkdownHref,
  previewTargetFromMarkdownHref,
  stripPreviewTargets
} from './preview-targets'

describe('preview target detection', () => {
  it('does not infer preview targets from raw paths or URLs', () => {
    expect(extractPreviewTargets('Preview: http://localhost:5173/')).toEqual([])
    expect(extractPreviewTargets('Open index.html\n/tmp/demo.html\nhttp://localhost:5173/')).toEqual([])
  })

  it('decodes preview markdown hrefs', () => {
    expect(previewTargetFromMarkdownHref('#preview/%2Ftmp%2Fdemo.html')).toBe('/tmp/demo.html')
    expect(previewTargetFromMarkdownHref('#preview:%2Ftmp%2Fdemo.html')).toBe('/tmp/demo.html')
    expect(previewTargetFromMarkdownHref('#media:%2Ftmp%2Fdemo.mp4')).toBeNull()
  })

  it('extracts preview targets from already-rendered preview markers', () => {
    expect(extractPreviewTargets('[Preview: demo.html](#preview:%2Ftmp%2Fdemo.html)')).toEqual(['/tmp/demo.html'])
  })

  it('strips preview targets from visible assistant text', () => {
    expect(stripPreviewTargets('ready\n/tmp/mycelium-bunnies.html\nopen it')).toBe(
      'ready\n/tmp/mycelium-bunnies.html\nopen it'
    )
    expect(stripPreviewTargets('[Preview: demo.html](#preview:%2Ftmp%2Fdemo.html)\nopen it')).toBe('open it')
  })

  it('round-trips bare-path hrefs', () => {
    expect(pathMarkdownHref('/tmp/a b.md')).toBe('#path/%2Ftmp%2Fa%20b.md')
    expect(pathFromMarkdownHref(pathMarkdownHref('~/notes/x.md'))).toBe('~/notes/x.md')
    expect(pathFromMarkdownHref('#preview/%2Ftmp%2Fdemo.html')).toBeNull()
    expect(pathFromMarkdownHref('#path/')).toBeNull()
    expect(pathFromMarkdownHref('#path/%E0%A4%A')).toBeNull()
    expect(pathFromMarkdownHref(undefined)).toBeNull()
  })
})
