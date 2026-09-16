import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $rightRailActiveTabId, RIGHT_RAIL_PREVIEW_TAB_ID } from './layout'
import {
  $filePreviewTabs,
  $filePreviewTabsBySession,
  $filePreviewTarget,
  $previewServerRestart,
  $previewServerRestartStatus,
  $previewTarget,
  $sessionPreviewRegistry,
  beginPreviewServerRestart,
  clearSessionPreviewRegistry,
  closeActiveRightRailTab,
  dismissPreviewTarget,
  forgetSessionPreviews,
  getSessionPreviewRecord,
  type PreviewTarget,
  progressPreviewServerRestart,
  setCurrentSessionPreviewTarget
} from './preview'
import { $activeSessionId, $selectedStoredSessionId } from './session'

function previewTarget(source: string): PreviewTarget {
  return {
    kind: 'file',
    label: source,
    path: source,
    previewKind: 'html',
    source,
    url: `file://${source}`
  }
}

function withRenderMode(target: PreviewTarget, renderMode: PreviewTarget['renderMode']): PreviewTarget {
  return { ...target, renderMode }
}

describe('preview store', () => {
  beforeEach(() => {
    $previewServerRestart.set(null)
    $activeSessionId.set('session-1')
    $selectedStoredSessionId.set(null)
    window.localStorage.clear()
    clearSessionPreviewRegistry()
  })

  afterEach(() => {
    $previewServerRestart.set(null)
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    window.localStorage.clear()
    clearSessionPreviewRegistry()
  })

  it('does not notify status subscribers for restart progress text', () => {
    const statuses: string[] = []
    const unsubscribe = $previewServerRestartStatus.subscribe(status => statuses.push(status))

    beginPreviewServerRestart('task-1', 'http://localhost:5174')
    progressPreviewServerRestart('task-1', 'first line')
    progressPreviewServerRestart('task-1', 'second line')
    unsubscribe()

    expect(statuses).toEqual(['idle', 'running'])
  })

  it('persists registered previews and dismissal per session', () => {
    const target = previewTarget('/work/demo.html')

    setCurrentSessionPreviewTarget(target, 'tool-result')

    expect($previewTarget.get()).toEqual(withRenderMode(target, 'preview'))
    expect(getSessionPreviewRecord('session-1')?.normalized).toEqual(withRenderMode(target, 'preview'))
    expect(window.localStorage.getItem('hermes.desktop.sessionPreviews.v1')).toContain('/work/demo.html')

    dismissPreviewTarget()

    expect($previewTarget.get()).toBeNull()
    expect(getSessionPreviewRecord('session-1')).toBeNull()
    expect($sessionPreviewRegistry.get()['session-1']?.[0]?.dismissedAt).toEqual(expect.any(Number))

    setCurrentSessionPreviewTarget(target, 'tool-result')

    expect(getSessionPreviewRecord('session-1')?.dismissedAt).toBeUndefined()
  })

  it('stores multi-tab session previews in session registry', () => {
    const first = previewTarget('/work/first.html')
    const second = previewTarget('/work/second.html')

    setCurrentSessionPreviewTarget(first, 'tool-result')
    setCurrentSessionPreviewTarget(second, 'tool-result')

    expect($sessionPreviewRegistry.get()['session-1']).toHaveLength(2)
    expect(getSessionPreviewRecord('session-1')?.normalized).toEqual(withRenderMode(second, 'preview'))

    dismissPreviewTarget()

    expect($previewTarget.get()).toBeNull()
    expect(getSessionPreviewRecord('session-1')).toBeNull()
    expect($sessionPreviewRegistry.get()['session-1']?.map(record => record.normalized.url)).toEqual([
      'file:///work/second.html',
      'file:///work/first.html'
    ])
  })

  it('keeps file inspection separate from live preview', () => {
    const target = previewTarget('/work/demo.html')
    const preview = previewTarget('/work/live.html')

    setCurrentSessionPreviewTarget(preview, 'tool-result')

    setCurrentSessionPreviewTarget(target, 'manual')

    expect($filePreviewTarget.get()).toEqual(withRenderMode(target, 'source'))
    expect($previewTarget.get()).toEqual(withRenderMode(preview, 'preview'))
    expect(getSessionPreviewRecord('session-1')?.normalized).toEqual(withRenderMode(preview, 'preview'))

    closeActiveRightRailTab()

    expect($filePreviewTarget.get()).toBeNull()
    expect($previewTarget.get()).toEqual(withRenderMode(preview, 'preview'))
  })

  it('keeps file tabs when a live preview opens', () => {
    const file = previewTarget('/work/file.html')
    const live = previewTarget('/work/live.html')

    setCurrentSessionPreviewTarget(file, 'manual')
    setCurrentSessionPreviewTarget(live, 'tool-result')

    expect($filePreviewTabs.get().map(tab => tab.target)).toEqual([withRenderMode(file, 'source')])
    expect($filePreviewTarget.get()).toBeNull()
    expect($rightRailActiveTabId.get()).toBe(RIGHT_RAIL_PREVIEW_TAB_ID)
    expect($previewTarget.get()).toEqual(withRenderMode(live, 'preview'))
  })

  it('accumulates multiple file tabs when multiple non-HTML files are emitted or inspected', () => {
    const fileA: PreviewTarget = {
      kind: 'file',
      label: 'a.ts',
      path: '/work/a.ts',
      previewKind: 'text',
      source: '/work/a.ts',
      url: 'file:///work/a.ts'
    }

    const fileB: PreviewTarget = {
      kind: 'file',
      label: 'b.json',
      path: '/work/b.json',
      previewKind: 'text',
      source: '/work/b.json',
      url: 'file:///work/b.json'
    }

    setCurrentSessionPreviewTarget(fileA, 'tool-result')
    setCurrentSessionPreviewTarget(fileB, 'tool-result')

    expect($filePreviewTabs.get().map(tab => tab.target.url)).toEqual(['file:///work/a.ts', 'file:///work/b.json'])
    expect($rightRailActiveTabId.get()).toBe('file:file:///work/b.json')
  })

  describe('per-chat file tabs (AIS-355)', () => {
    const tabIds = () => $filePreviewTabs.get().map(tab => tab.id)

    it("shows only the active chat's file tabs and restores them, focus included, on return", () => {
      setCurrentSessionPreviewTarget(previewTarget('/work/a.html'), 'manual')
      setCurrentSessionPreviewTarget(previewTarget('/work/b.html'), 'manual')
      $rightRailActiveTabId.set('file:file:///work/a.html')

      expect(tabIds()).toEqual(['file:file:///work/a.html', 'file:file:///work/b.html'])

      $activeSessionId.set('session-2')

      expect(tabIds()).toEqual([])
      expect($rightRailActiveTabId.get()).toBe(RIGHT_RAIL_PREVIEW_TAB_ID)

      setCurrentSessionPreviewTarget(previewTarget('/work/c.html'), 'manual')

      expect(tabIds()).toEqual(['file:file:///work/c.html'])

      $activeSessionId.set('session-1')

      expect(tabIds()).toEqual(['file:file:///work/a.html', 'file:file:///work/b.html'])
      expect($rightRailActiveTabId.get()).toBe('file:file:///work/a.html')
      expect(Object.keys($filePreviewTabsBySession.get()).sort()).toEqual(['session-1', 'session-2'])
    })

    it('prefers the stored session id over the live id, like the live-preview registry', () => {
      $selectedStoredSessionId.set('stored-1')
      setCurrentSessionPreviewTarget(previewTarget('/work/a.html'), 'manual')

      expect($filePreviewTabsBySession.get()['stored-1']?.tabs).toHaveLength(1)

      $selectedStoredSessionId.set('stored-2')

      expect(tabIds()).toEqual([])

      $selectedStoredSessionId.set('stored-1')

      expect(tabIds()).toEqual(['file:file:///work/a.html'])
    })

    it('keeps the tabs of a draft that just received its session id', () => {
      $activeSessionId.set(null)
      setCurrentSessionPreviewTarget(previewTarget('/work/draft.html'), 'manual')

      expect(tabIds()).toEqual(['file:file:///work/draft.html'])

      $activeSessionId.set('session-9')

      expect(tabIds()).toEqual(['file:file:///work/draft.html'])
      expect($filePreviewTabsBySession.get()['']).toBeUndefined()
      expect($filePreviewTabsBySession.get()['session-9']?.tabs).toHaveLength(1)
    })

    it("closing a tab or the rail updates the chat's record", () => {
      setCurrentSessionPreviewTarget(previewTarget('/work/a.html'), 'manual')
      setCurrentSessionPreviewTarget(previewTarget('/work/b.html'), 'manual')

      closeActiveRightRailTab()

      expect($filePreviewTabsBySession.get()['session-1']?.tabs.map(tab => tab.id)).toEqual([
        'file:file:///work/a.html'
      ])

      closeActiveRightRailTab()

      expect($filePreviewTabsBySession.get()['session-1']).toBeUndefined()
    })

    it("forgets a deleted chat's tabs and live-preview records", () => {
      setCurrentSessionPreviewTarget(previewTarget('/work/a.html'), 'manual')
      setCurrentSessionPreviewTarget(previewTarget('/work/live.html'), 'tool-result')

      expect(getSessionPreviewRecord('session-1')).not.toBeNull()

      forgetSessionPreviews('session-1')

      expect(tabIds()).toEqual([])
      expect($filePreviewTabsBySession.get()['session-1']).toBeUndefined()
      expect(getSessionPreviewRecord('session-1')).toBeNull()
    })

    it('persists per-chat tabs without inline bytes', () => {
      setCurrentSessionPreviewTarget(previewTarget('/work/a.html'), 'manual')
      setCurrentSessionPreviewTarget(
        { ...previewTarget('/tmp/shot.png'), dataUrl: 'data:image/png;base64,AAAA', previewKind: 'image' },
        'manual'
      )

      const raw = window.localStorage.getItem('hermes.desktop.filePreviewTabs.v1') ?? ''

      expect(raw).toContain('/work/a.html')
      expect(raw).not.toContain('shot.png')
      expect(raw).not.toContain('base64')
    })
  })
})
