import { describe, expect, it } from 'vitest'

import { splitFrontmatter } from './markdown-frontmatter'

describe('splitFrontmatter', () => {
  it('splits flat frontmatter into properties and keeps the body', () => {
    const result = splitFrontmatter(
      [
        '---',
        'type: proposal',
        'title: Strategisches Migrationskonzept & Infrastruktur-Modernisierung',
        'version: 1.0',
        "status: 'draft'",
        'tags: [proposal, migration, netcup]',
        'aliases:',
        '  - Umzug',
        '  - Migration',
        '---',
        '',
        '# Strategisches Migrationskonzept'
      ].join('\n')
    )

    expect(result?.properties).toEqual([
      ['type', 'proposal'],
      ['title', 'Strategisches Migrationskonzept & Infrastruktur-Modernisierung'],
      ['version', '1.0'],
      ['status', 'draft'],
      ['tags', 'proposal, migration, netcup'],
      ['aliases', 'Umzug, Migration']
    ])
    expect(result?.body).toBe('# Strategisches Migrationskonzept')
  })

  it('keeps nested YAML as raw text without properties', () => {
    const result = splitFrontmatter('---\nowner:\n  name: Jo\n---\nBody')

    expect(result?.properties).toBeNull()
    expect(result?.raw).toBe('owner:\n  name: Jo')
    expect(result?.body).toBe('Body')
  })

  it('ignores documents without leading frontmatter', () => {
    expect(splitFrontmatter('# Title\n\n---\n\ntext')).toBeNull()
    expect(splitFrontmatter('text\n---\nkey: value\n---')).toBeNull()
  })
})
