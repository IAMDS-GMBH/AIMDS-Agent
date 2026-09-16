import { atom, computed } from 'nanostores'

import { $rightRailActiveTabId, RIGHT_RAIL_PREVIEW_TAB_ID, type RightRailTabId, selectRightRailTab } from './layout'
import { $activeSessionId, $selectedStoredSessionId } from './session'

export interface PreviewTarget {
  binary?: boolean
  byteSize?: number
  /** Inline image bytes (a `data:` URL) when the renderer already holds them —
   * e.g. a pasted/dropped screenshot whose only on-disk copy is a transient
   * path the preview can't reliably re-read. Rendered directly and NOT
   * persisted to the session-preview registry (it would bloat localStorage). */
  dataUrl?: string
  /** Inline text content when the renderer already holds it — e.g. a cron
   * artifact fetched through the backend because the path isn't readable on
   * this machine (remote backend). Rendered directly, never persisted. */
  text?: string
  kind: 'file' | 'url'
  label: string
  large?: boolean
  language?: string
  mimeType?: string
  path?: string
  previewKind?: 'binary' | 'html' | 'image' | 'text'
  renderMode?: 'preview' | 'source'
  source: string
  url: string
}

export interface PreviewServerRestart {
  message?: string
  status: 'complete' | 'error' | 'running'
  taskId: string
  url: string
}

export type PreviewRecordSource = 'explicit-link' | 'file-browser' | 'manual' | 'tool-result'

export interface SessionPreviewRecord {
  autoOpen?: boolean
  createdAt: number
  dismissedAt?: number
  id: string
  normalized: PreviewTarget
  sessionId: string
  source: PreviewRecordSource
  target: string
}

type SessionPreviewRegistry = Record<string, SessionPreviewRecord[]>

export interface FilePreviewTab {
  id: `file:${string}` | `url:${string}`
  target: PreviewTarget
}

/** The file/url tabs one chat had open, plus which rail tab it was looking at. */
export interface SessionFilePreviewTabs {
  activeTabId: RightRailTabId
  tabs: FilePreviewTab[]
}

type FilePreviewTabsBySession = Record<string, SessionFilePreviewTabs>

const REGISTRY_STORAGE_KEY = 'hermes.desktop.sessionPreviews.v1'
const FILE_TABS_STORAGE_KEY = 'hermes.desktop.filePreviewTabs.v1'
const MAX_RECORDS_PER_SESSION = 12
const MAX_FILE_TABS_PER_SESSION = 24
const MAX_SESSIONS = 120

export const $previewTarget = atom<PreviewTarget | null>(null)
/** File/url tabs of the chat currently shown. Swapped in and out per session
 * by the listener below, so a tab opened in one chat never lingers in another
 * (AIS-355). */
export const $filePreviewTabs = atom<FilePreviewTab[]>([])
/** Every chat's file tabs, keyed like the session preview registry. */
export const $filePreviewTabsBySession = atom<FilePreviewTabsBySession>(loadFilePreviewTabsBySession())
/** The id the preview stores file their per-chat state under: the stored id of
 * a resumed chat, else the live id, else '' for a draft that has no id yet. */
export const $previewSessionKey = computed(
  [$selectedStoredSessionId, $activeSessionId],
  (stored, active) => stored || active || ''
)
export const $filePreviewTarget = computed([$filePreviewTabs, $rightRailActiveTabId], (tabs, activeTabId) => {
  if (!activeTabId.startsWith('file:')) {
    return null
  }

  return tabs.find(tab => tab.id === activeTabId)?.target ?? null
})
export const $previewReloadRequest = atom(0)
export const $previewServerRestart = atom<PreviewServerRestart | null>(null)
export const $previewServerRestartStatus = computed($previewServerRestart, restart => restart?.status ?? 'idle')
export const $sessionPreviewRegistry = atom<SessionPreviewRegistry>(loadSessionPreviewRegistry())

$sessionPreviewRegistry.subscribe(persistSessionPreviewRegistry)
$filePreviewTabsBySession.subscribe(persistFilePreviewTabsBySession)

let restoringFilePreviewTabs = false

// Mirror the visible tabs (and the tab in focus) into the per-chat record as
// they change, so switching away can never lose them.
$filePreviewTabs.listen(tabs => {
  if (!restoringFilePreviewTabs) {
    rememberFilePreviewTabs(currentPreviewSessionId(), tabs, $rightRailActiveTabId.get())
  }
})

$rightRailActiveTabId.listen(activeTabId => {
  if (restoringFilePreviewTabs) {
    return
  }

  const key = currentPreviewSessionId()
  const entry = $filePreviewTabsBySession.get()[key]

  if (entry && entry.activeTabId !== activeTabId) {
    $filePreviewTabsBySession.set({ ...$filePreviewTabsBySession.get(), [key]: { ...entry, activeTabId } })
  }
})

// Chat switch: the previous chat's tabs are already mirrored; show the next
// chat's tabs (or none). A draft that just received its id keeps its tabs —
// they were opened in this very conversation, only the key changed.
$previewSessionKey.listen((next, previous) => {
  if (previous === '' && next !== '' && !$filePreviewTabsBySession.get()[next]) {
    const draft = $filePreviewTabsBySession.get()['']

    if (draft) {
      const { '': _draft, ...rest } = $filePreviewTabsBySession.get()

      $filePreviewTabsBySession.set({ ...rest, [next]: draft })
    }

    return
  }

  restoreFilePreviewTabs(next)
})

function isSamePreviewTarget(a: PreviewTarget | null, b: PreviewTarget | null): boolean {
  if (a === b) {
    return true
  }

  if (!a || !b) {
    return false
  }

  return (
    a.kind === b.kind &&
    a.label === b.label &&
    a.renderMode === b.renderMode &&
    a.source === b.source &&
    a.url === b.url
  )
}

export function setPreviewTarget(target: PreviewTarget | null) {
  if (isSamePreviewTarget($previewTarget.get(), target)) {
    if (target) {
      selectRightRailTab(RIGHT_RAIL_PREVIEW_TAB_ID)
    }

    return
  }

  $previewTarget.set(target)

  if (target) {
    selectRightRailTab(RIGHT_RAIL_PREVIEW_TAB_ID)
  }
}

export function filePreviewTabId(target: PreviewTarget): `file:${string}` | `url:${string}` {
  return target.kind === 'url' ? `url:${target.url}` : `file:${target.url}`
}

function openFilePreviewTarget(target: PreviewTarget) {
  const id = filePreviewTabId(target)
  const current = $filePreviewTabs.get()
  const index = current.findIndex(tab => tab.id === id)
  const tab: FilePreviewTab = { id, target }

  $filePreviewTabs.set(index === -1 ? [...current, tab] : current.map((item, i) => (i === index ? tab : item)))
  selectRightRailTab(id)
}

// Manual/file-browser opens are "peeking at a file" → source view in the file
// pane. Tool/explicit-link opens are runnable artifacts → live preview pane.
function isFilePreviewSource(source: PreviewRecordSource): boolean {
  return source === 'file-browser' || source === 'manual'
}

function previewTargetForSource(target: PreviewTarget, source: PreviewRecordSource): PreviewTarget {
  if (target.kind !== 'file' || target.previewKind !== 'html') {
    return target
  }

  return { ...target, renderMode: isFilePreviewSource(source) ? 'source' : 'preview' }
}

function tryOpenFilePreview(target: PreviewTarget, source: PreviewRecordSource): boolean {
  if (target.kind !== 'file') {
    return false
  }

  if (isFilePreviewSource(source) || target.previewKind !== 'html') {
    openFilePreviewTarget(previewTargetForSource(target, source))

    return true
  }

  return false
}

function isPreviewTarget(value: unknown): value is PreviewTarget {
  if (!value || typeof value !== 'object') {
    return false
  }

  const r = value as Record<string, unknown>

  return (
    (r.kind === 'file' || r.kind === 'url') &&
    typeof r.label === 'string' &&
    typeof r.source === 'string' &&
    typeof r.url === 'string'
  )
}

function isPreviewRecord(value: unknown): value is SessionPreviewRecord {
  if (!value || typeof value !== 'object') {
    return false
  }

  const r = value as Record<string, unknown>

  return (
    typeof r.createdAt === 'number' &&
    typeof r.id === 'string' &&
    isPreviewTarget(r.normalized) &&
    typeof r.sessionId === 'string' &&
    ['explicit-link', 'file-browser', 'manual', 'tool-result'].includes(String(r.source)) &&
    typeof r.target === 'string' &&
    (r.dismissedAt === undefined || typeof r.dismissedAt === 'number')
  )
}

function loadSessionPreviewRegistry(): SessionPreviewRegistry {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(REGISTRY_STORAGE_KEY)

    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as unknown

    if (!parsed || typeof parsed !== 'object') {
      return {}
    }

    const out: SessionPreviewRegistry = {}

    for (const [sessionId, records] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(records)) {
        continue
      }

      const valid = records.filter(isPreviewRecord).slice(0, MAX_RECORDS_PER_SESSION)

      if (valid.length > 0) {
        out[sessionId] = valid
      }
    }

    return pruneRegistry(out)
  } catch {
    return {}
  }
}

function persistSessionPreviewRegistry(registry: SessionPreviewRegistry) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    // Drop the inline image bytes before persisting — a screenshot data URL is
    // megabytes and would blow the localStorage quota. On reload the record
    // falls back to reading its `path`/`url`.
    const lean = JSON.stringify(pruneRegistry(registry), (key, value) =>
      key === 'dataUrl' || key === 'text' ? undefined : value
    )

    window.localStorage.setItem(REGISTRY_STORAGE_KEY, lean)
  } catch {
    // Session previews are a desktop convenience; storage failures are nonfatal.
  }
}

function pruneRegistry(registry: SessionPreviewRegistry): SessionPreviewRegistry {
  const entries = Object.entries(registry)
    .map(
      ([sessionId, records]) =>
        [sessionId, [...records].sort((a, b) => b.createdAt - a.createdAt).slice(0, MAX_RECORDS_PER_SESSION)] as const
    )
    .filter(([, records]) => records.length > 0)
    .sort(([, a], [, b]) => (b[0]?.createdAt ?? 0) - (a[0]?.createdAt ?? 0))
    .slice(0, MAX_SESSIONS)

  return Object.fromEntries(entries)
}

function currentPreviewSessionId(): string {
  return $previewSessionKey.get()
}

function isFilePreviewTab(value: unknown): value is FilePreviewTab {
  if (!value || typeof value !== 'object') {
    return false
  }

  const r = value as Record<string, unknown>

  return typeof r.id === 'string' && (r.id.startsWith('file:') || r.id.startsWith('url:')) && isPreviewTarget(r.target)
}

function loadFilePreviewTabsBySession(): FilePreviewTabsBySession {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(FILE_TABS_STORAGE_KEY)

    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as unknown

    if (!parsed || typeof parsed !== 'object') {
      return {}
    }

    const out: FilePreviewTabsBySession = {}

    for (const [sessionId, entry] of Object.entries(parsed as Record<string, unknown>)) {
      if (!sessionId || !entry || typeof entry !== 'object') {
        continue
      }

      const { activeTabId, tabs } = entry as Record<string, unknown>

      if (!Array.isArray(tabs)) {
        continue
      }

      const valid = tabs.filter(isFilePreviewTab).slice(0, MAX_FILE_TABS_PER_SESSION)

      if (valid.length > 0) {
        out[sessionId] = {
          activeTabId: typeof activeTabId === 'string' ? (activeTabId as RightRailTabId) : RIGHT_RAIL_PREVIEW_TAB_ID,
          tabs: valid
        }
      }
    }

    return out
  } catch {
    return {}
  }
}

function persistFilePreviewTabsBySession(bySession: FilePreviewTabsBySession) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    // Tabs whose content only lives in memory (a pasted screenshot, text
    // fetched from a remote backend) cannot be re-read after a reload, and the
    // bytes would blow the localStorage quota — they stay session-only.
    const lean = Object.entries(bySession)
      .map(([sessionId, entry]) => {
        const tabs = entry.tabs.filter(tab => !tab.target.dataUrl && !tab.target.text)

        const activeStillOpen =
          entry.activeTabId === RIGHT_RAIL_PREVIEW_TAB_ID || tabs.some(tab => tab.id === entry.activeTabId)

        return [
          sessionId,
          { activeTabId: activeStillOpen ? entry.activeTabId : RIGHT_RAIL_PREVIEW_TAB_ID, tabs }
        ] as const
      })
      .filter(([sessionId, entry]) => sessionId && entry.tabs.length > 0)
      .slice(-MAX_SESSIONS)

    window.localStorage.setItem(FILE_TABS_STORAGE_KEY, JSON.stringify(Object.fromEntries(lean)))
  } catch {
    // Per-chat tabs are a convenience; storage failures are nonfatal.
  }
}

function rememberFilePreviewTabs(key: string, tabs: readonly FilePreviewTab[], activeTabId: RightRailTabId) {
  const current = $filePreviewTabsBySession.get()

  if (tabs.length === 0) {
    if (key in current) {
      const { [key]: _gone, ...rest } = current

      $filePreviewTabsBySession.set(rest)
    }

    return
  }

  $filePreviewTabsBySession.set({ ...current, [key]: { activeTabId, tabs: tabs.slice(-MAX_FILE_TABS_PER_SESSION) } })
}

function restoreFilePreviewTabs(key: string) {
  const entry = $filePreviewTabsBySession.get()[key]
  const tabs = entry?.tabs ?? []

  restoringFilePreviewTabs = true

  try {
    $filePreviewTabs.set(tabs)
  } finally {
    restoringFilePreviewTabs = false
  }

  const wanted = entry?.activeTabId
  const wantedStillOpen = wanted === RIGHT_RAIL_PREVIEW_TAB_ID || tabs.some(tab => tab.id === wanted)

  selectRightRailTab(wanted && wantedStillOpen ? wanted : (tabs[0]?.id ?? RIGHT_RAIL_PREVIEW_TAB_ID))
}

/** A chat was deleted: drop its file tabs and its live-preview records. */
export function forgetSessionPreviews(sessionId: string | null | undefined) {
  const id = sessionId?.trim()

  if (!id) {
    return
  }

  const { [id]: _tabs, ...restTabs } = $filePreviewTabsBySession.get()
  const { [id]: _records, ...restRecords } = $sessionPreviewRegistry.get()

  $filePreviewTabsBySession.set(restTabs)
  $sessionPreviewRegistry.set(restRecords)

  if (id === currentPreviewSessionId()) {
    $filePreviewTabs.set([])
  }
}

function recordId(sessionId: string, target: PreviewTarget): string {
  return `${sessionId}:${target.url}`
}

export function registerSessionPreview(
  sessionId: string | null | undefined,
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  const id = sessionId?.trim()

  if (!id) {
    return null
  }

  const current = $sessionPreviewRegistry.get()
  const now = Date.now()
  const records = current[id] ?? []
  const existing = records.find(record => record.normalized.url === target.url)
  const filtered = records.filter(record => record.normalized.url !== target.url)
  const normalized = previewTargetForSource(target, source)

  const nextRecord: SessionPreviewRecord = {
    autoOpen: true,
    createdAt: now,
    id: existing?.id || recordId(id, target),
    normalized,
    sessionId: id,
    source,
    target: rawTarget || target.source
  }

  $sessionPreviewRegistry.set(
    pruneRegistry({
      ...current,
      [id]: [nextRecord, ...filtered]
    })
  )

  return nextRecord
}

export function setSessionPreviewTarget(
  sessionId: string | null | undefined,
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  // Route first, register second. The session registry tracks what the session
  // is *live-previewing*; a target that belongs in the file lane (peeked at, or
  // not renderable) must not be written there. Registering before the routing
  // decision let a file inspection overwrite the session's live preview record,
  // so reopening the session restored the inspected file instead.
  if (tryOpenFilePreview(target, source)) {
    return null
  }

  const record = registerSessionPreview(sessionId, target, source, rawTarget)

  setPreviewTarget(record?.normalized ?? previewTargetForSource(target, source))

  return record
}

export function setCurrentSessionPreviewTarget(
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  return setSessionPreviewTarget(currentPreviewSessionId(), target, source, rawTarget)
}

export function getSessionPreviewRecord(sessionId: string | null | undefined): SessionPreviewRecord | null {
  const id = sessionId?.trim()

  if (!id) {
    return null
  }

  return $sessionPreviewRegistry.get()[id]?.find(record => !record.dismissedAt && record.autoOpen !== false) ?? null
}

export function dismissSessionPreview(sessionId: string | null | undefined, url?: string) {
  const id = sessionId?.trim()

  if (!id) {
    return
  }

  const current = $sessionPreviewRegistry.get()
  const records = current[id]

  if (!records?.length) {
    return
  }

  const now = Date.now()
  const targetUrl = url || records.find(record => !record.dismissedAt)?.normalized.url

  if (!targetUrl) {
    return
  }

  // The preview rail is a single active file, not a back stack. Dismissing the
  // current preview should leave the rail closed instead of revealing an older
  // record for the same session.
  const dismissedRecords = records.map(record => ({
    ...record,
    autoOpen: false,
    dismissedAt: now
  }))

  $sessionPreviewRegistry.set({
    ...current,
    [id]: dismissedRecords
  })
}

/** User clicked the close X — clear the target and persist dismissal for the current session. */
export function dismissPreviewTarget() {
  const current = $previewTarget.get()

  if (current?.url) {
    dismissSessionPreview(currentPreviewSessionId(), current.url)
  }

  $previewTarget.set(null)

  if ($rightRailActiveTabId.get() === RIGHT_RAIL_PREVIEW_TAB_ID) {
    selectRightRailTab($filePreviewTabs.get()[0]?.id ?? RIGHT_RAIL_PREVIEW_TAB_ID)
  }
}

function closeFilePreviewTab(tabId: RightRailTabId) {
  if (!tabId.startsWith('file:') && !tabId.startsWith('url:')) {
    return
  }

  const current = $filePreviewTabs.get()
  const index = current.findIndex(tab => tab.id === tabId)

  if (index === -1) {
    return
  }

  const next = current.filter(tab => tab.id !== tabId)

  $filePreviewTabs.set(next)

  if ($rightRailActiveTabId.get() === tabId) {
    selectRightRailTab(next[Math.min(index, next.length - 1)]?.id ?? RIGHT_RAIL_PREVIEW_TAB_ID)
  }
}

export function closeRightRailTab(tabId: RightRailTabId) {
  if (tabId === RIGHT_RAIL_PREVIEW_TAB_ID) {
    if ($previewTarget.get()) {
      dismissPreviewTarget()
    }

    return
  }

  closeFilePreviewTab(tabId)
}

export const closeActiveRightRailTab = () => closeRightRailTab($rightRailActiveTabId.get())

/** Dismisses the active preview + every file tab so the rail pane unmounts. */
export function closeRightRail() {
  if ($previewTarget.get()) {
    dismissPreviewTarget()
  }

  $filePreviewTabs.set([])
}

export function clearSessionPreviewRegistry() {
  $sessionPreviewRegistry.set({})
  setPreviewTarget(null)
  $filePreviewTabs.set([])
  $filePreviewTabsBySession.set({})
  selectRightRailTab(RIGHT_RAIL_PREVIEW_TAB_ID)
}

export function requestPreviewReload() {
  $previewReloadRequest.set($previewReloadRequest.get() + 1)
}

export function beginPreviewServerRestart(taskId: string, url: string) {
  $previewServerRestart.set({ status: 'running', taskId, url })
}

export function completePreviewServerRestart(taskId: string, text: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId) {
    return
  }

  $previewServerRestart.set({
    ...current,
    message: text,
    status: text.trim().toLowerCase().startsWith('error:') ? 'error' : 'complete'
  })
}

export function progressPreviewServerRestart(taskId: string, text: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId || current.status !== 'running') {
    return
  }

  $previewServerRestart.set({
    ...current,
    message: text
  })
}

export function failPreviewServerRestart(taskId: string, message: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId || current.status !== 'running') {
    return
  }

  $previewServerRestart.set({
    ...current,
    message,
    status: 'error'
  })
}
