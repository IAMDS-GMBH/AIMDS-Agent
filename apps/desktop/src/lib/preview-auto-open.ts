import type { RpcEvent } from '@/types/hermes'

export type PreviewAutoOpenMode = 'artifacts' | 'all' | 'never'

// Tools that only *look at* files. Their arguments carry paths, but a read is
// never a reason to pop the preview pane (AIS-305 B: "read_file opened the
// preview for every file the agent looked at").
const READ_TOOL_RE = /(^|_)(read|search|grep|glob|list|ls|find|cat|view|stat|tree|fetch|get|query|open)(_|$)/i

// Tools that produce something worth looking at.
const ARTIFACT_TOOL_RE = /(write|create|edit|patch|apply|save|export|render|generate|screenshot|build|journal|note)/i

// Files a person would want to see rendered; code/config/log files are not
// previews, they're edits — the diff card already covers them.
const ARTIFACT_EXT_RE = /\.(html?|svg|pdf|png|jpe?g|webp|gif|docx?|xlsx?|pptx?|md|markdown|csv)(?:[?#].*)?$/i

const LOCAL_URL_RE = /^https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:\/|$)/i

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

export function toolNameFromPayload(payload: unknown): string {
  const record = asRecord(payload)

  for (const field of ['name', 'tool_name', 'tool', 'function']) {
    const value = record[field]

    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }

  return ''
}

function hasInlineDiff(payload: unknown): boolean {
  const diff = asRecord(payload).inline_diff

  return typeof diff === 'string' && diff.trim().length > 0
}

function hasError(payload: unknown): boolean {
  const error = asRecord(payload).error

  if (typeof error === 'string') {
    return error.trim().length > 0
  }

  return Boolean(error)
}

export function isLocalHttpUrl(candidate: string): boolean {
  return LOCAL_URL_RE.test(candidate.trim())
}

export function hasArtifactExtension(candidate: string): boolean {
  const value = candidate.trim()

  if (/^https?:\/\//i.test(value)) {
    try {
      return ARTIFACT_EXT_RE.test(new URL(value).pathname)
    } catch {
      return false
    }
  }

  return ARTIFACT_EXT_RE.test(value)
}

export function isReadOnlyToolName(name: string): boolean {
  return Boolean(name) && READ_TOOL_RE.test(name)
}

export function isArtifactToolName(name: string): boolean {
  return Boolean(name) && ARTIFACT_TOOL_RE.test(name)
}

// Decide whether a structured tool event may auto-open `candidate` in the
// preview pane.
//   never      → nothing opens by itself
//   artifacts  → producing tools (or an inline diff) writing a document /
//                page / image, or a localhost URL (dev server)
//   all        → the old behaviour minus tool.start and read-only tools
// Errors never open anything, and tool.start never does (the file doesn't
// exist yet; the completion carries the real path).
export function shouldAutoOpenToolPreview(event: RpcEvent, candidate: string, mode: PreviewAutoOpenMode): boolean {
  if (mode === 'never' || !candidate.trim()) {
    return false
  }

  if (event.type === 'tool.start') {
    return false
  }

  if (hasError(event.payload)) {
    return false
  }

  const name = toolNameFromPayload(event.payload)

  if (isReadOnlyToolName(name)) {
    return false
  }

  if (mode === 'all') {
    return true
  }

  if (isLocalHttpUrl(candidate)) {
    return true
  }

  if (!isArtifactToolName(name) && !hasInlineDiff(event.payload)) {
    return false
  }

  return hasArtifactExtension(candidate)
}
