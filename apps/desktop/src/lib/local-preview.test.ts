import { describe, expect, it } from 'vitest'

import { localPreviewTarget } from './local-preview'

describe('localPreviewTarget preview kinds', () => {
  it.each([
    ['/tmp/a.pdf', 'pdf'],
    ['/tmp/a.docx', 'document'],
    ['/tmp/a.xlsx', 'document'],
    ['/tmp/a.pptx', 'document'],
    ['/tmp/a.odt', 'document'],
    ['/tmp/a.heic', 'image'],
    ['/tmp/a.tiff', 'image'],
    ['/tmp/a.avif', 'image'],
    ['/tmp/a.html', 'html'],
    ['/tmp/a.csv', 'text']
  ])('%s → %s', (path, kind) => {
    expect(localPreviewTarget(path)?.previewKind).toBe(kind)
  })
})
