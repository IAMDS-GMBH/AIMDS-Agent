import { describe, expect, it } from 'vitest'

import type { RpcEvent } from '@/types/hermes'

import { hasArtifactExtension, isLocalHttpUrl, shouldAutoOpenToolPreview } from './preview-auto-open'

function event(type: string, payload: Record<string, unknown> = {}): RpcEvent {
  return { payload, session_id: 's1', type }
}

describe('shouldAutoOpenToolPreview', () => {
  it('never opens in never mode', () => {
    expect(shouldAutoOpenToolPreview(event('tool.complete', { name: 'write_file' }), '/tmp/a.html', 'never')).toBe(
      false
    )
  })

  it('never opens on tool.start or on errors', () => {
    expect(shouldAutoOpenToolPreview(event('tool.start', { name: 'write_file' }), '/tmp/a.html', 'all')).toBe(false)
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { name: 'write_file', error: 'EACCES' }), '/tmp/a.html', 'all')
    ).toBe(false)
  })

  it('blocks read-only tools in every mode', () => {
    for (const name of [
      'read_file',
      'search_files',
      'grep',
      'list_dir',
      'ls',
      'cat_file',
      'fetch_url',
      'get_note',
      'open'
    ]) {
      expect(shouldAutoOpenToolPreview(event('tool.complete', { name }), '/tmp/a.md', 'all')).toBe(false)
      expect(shouldAutoOpenToolPreview(event('tool.complete', { name }), '/tmp/a.md', 'artifacts')).toBe(false)
    }
  })

  it('all mode opens everything else, including code files', () => {
    expect(shouldAutoOpenToolPreview(event('tool.complete', { name: 'edit_file' }), '/tmp/a.py', 'all')).toBe(true)
    expect(shouldAutoOpenToolPreview(event('tool.complete', {}), '/tmp/a.py', 'all')).toBe(true)
  })

  it('artifacts mode requires a producing tool and an artifact extension', () => {
    expect(shouldAutoOpenToolPreview(event('tool.complete', { name: 'write_file' }), '/tmp/a.md', 'artifacts')).toBe(
      true
    )
    expect(shouldAutoOpenToolPreview(event('tool.complete', { name: 'write_file' }), '/tmp/a.py', 'artifacts')).toBe(
      false
    )
    expect(shouldAutoOpenToolPreview(event('tool.complete', { name: 'terminal' }), '/tmp/a.md', 'artifacts')).toBe(
      false
    )
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { tool_name: 'journal_write' }), '/tmp/a.md', 'artifacts')
    ).toBe(true)
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { name: 'screenshot' }), '/tmp/shot.png', 'artifacts')
    ).toBe(true)
  })

  it('artifacts mode accepts an inline diff as proof of an edit', () => {
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { inline_diff: 'a/x.html -> b/x.html' }), 'x.html', 'artifacts')
    ).toBe(true)
  })

  it('artifacts mode always opens localhost URLs', () => {
    expect(shouldAutoOpenToolPreview(event('tool.complete', {}), 'http://localhost:5173/', 'artifacts')).toBe(true)
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { name: 'terminal' }), 'http://127.0.0.1:3000', 'artifacts')
    ).toBe(true)
    expect(
      shouldAutoOpenToolPreview(event('tool.complete', { name: 'terminal' }), 'https://example.com/', 'artifacts')
    ).toBe(false)
  })
})

describe('helpers', () => {
  it('detects local urls', () => {
    expect(isLocalHttpUrl('http://localhost:8080/x')).toBe(true)
    expect(isLocalHttpUrl('http://[::1]:8080')).toBe(true)
    expect(isLocalHttpUrl('http://localhost.evil.com')).toBe(false)
  })

  it('checks artifact extensions on paths and url pathnames', () => {
    expect(hasArtifactExtension('/tmp/report.PDF')).toBe(true)
    expect(hasArtifactExtension('/tmp/report.md?x=1')).toBe(true)
    expect(hasArtifactExtension('https://x.test/docs/report.docx')).toBe(true)
    expect(hasArtifactExtension('https://x.test/')).toBe(false)
    expect(hasArtifactExtension('/tmp/a.ts')).toBe(false)
  })
})
