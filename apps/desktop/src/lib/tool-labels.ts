import { getRuntimeI18nLocale, type ToolActivityCopy, type ToolActivityPlace, TRANSLATIONS } from '@/i18n'

// Readable step labels for the chat (AIS-744). A tool row used to read
// "Mcp AIMDSSuiteMCP Mcp Memory Memory Read"; end users need "Opening notes in
// memory". Labels are derived generically from the tool's own words — area
// (OpenProject, memory, mail …), action (search, list, get …) and object
// (work packages, time entries …) — so a new MCP tool gets a sentence without
// a per-tool entry. Only the vocabulary lives in the i18n catalog.

type VerbKey = keyof ToolActivityCopy['verbs']

export type ToolLabelKind = 'builtin' | 'internal' | 'mcp'

export interface ToolLabel {
  /** Display name of the service touched, for the activity summary. */
  area?: string
  done: string
  kind: ToolLabelKind
  pending: string
  /** Where the step happens; used for "OpenProject is responding slowly". */
  place?: ToolActivityPlace
}

const AREA_TOKENS: Record<string, string> = {
  calendar: 'calendar',
  chat: 'teams',
  customer: 'documents',
  docling: 'documents',
  drive: 'files',
  email: 'mail',
  inbox: 'mail',
  jira: 'jira',
  mail: 'mail',
  memory: 'memory',
  ntfy: 'notifications',
  onedrive: 'files',
  openproject: 'openproject',
  outlook: 'mail',
  pm: 'openproject',
  sharepoint: 'files',
  storage: 'documents',
  teams: 'teams',
  tempo: 'tempo',
  timesheet: 'worktime',
  websearch: 'web',
  worktime: 'worktime'
}

const VERB_TOKENS: Record<string, VerbKey> = {
  add: 'create',
  browse: 'list',
  context: 'read',
  create: 'create',
  delete: 'delete',
  describe: 'read',
  edit: 'update',
  fetch: 'read',
  find: 'search',
  get: 'read',
  ingest: 'create',
  link: 'update',
  list: 'list',
  lookup: 'search',
  meta: 'read',
  move: 'update',
  new: 'create',
  patch: 'update',
  post: 'create',
  query: 'search',
  read: 'read',
  recent: 'list',
  remove: 'delete',
  rename: 'update',
  reply: 'create',
  retrieve: 'list',
  save: 'create',
  search: 'search',
  send: 'create',
  set: 'update',
  show: 'read',
  status: 'read',
  summary: 'read',
  trash: 'delete',
  unlink: 'delete',
  update: 'update',
  upload: 'create',
  view: 'read'
}

// Tools that only prepare the agent's own work (finding a tool, loading
// context). They are counted, but never shown with technical names.
const PREPARE_TOOLS = new Set(['skill_view', 'skills_list', 'tool_describe', 'tool_search'])
const ANALYZE_TOOLS = new Set(['execute_sql', 'sql'])

// Builtin steps that touch a known area, for the activity summary.
const STEP_AREAS: Record<string, string> = {
  web_extract: 'web',
  web_search: 'web',
  workdays: 'worktime'
}

const DETAIL_KEYS = [
  'query',
  'search',
  'search_term',
  'work_package_id',
  'key',
  'subject',
  'title',
  'id',
  'url',
  'path'
]

function activityCopy(copy?: ToolActivityCopy): ToolActivityCopy {
  return copy ?? TRANSLATIONS[getRuntimeI18nLocale()].assistant.tool.activity
}

function record(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }

  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)

      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }

  return {}
}

function stringField(args: Record<string, unknown>, key: string): string {
  const value = args[key]

  if (typeof value === 'string') {
    return value.trim()
  }

  return typeof value === 'number' ? String(value) : ''
}

function tokens(value: string): string[] {
  return value
    .replace(/([a-z])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
}

function plural(word: string): string {
  if (word.endsWith('s')) {
    return word
  }

  return /[^aeiou]y$/.test(word) ? `${word.slice(0, -1)}ies` : `${word}s`
}

function objectLabel(words: string[], place: ToolActivityPlace, copy: ToolActivityCopy): string {
  if (words.length === 0) {
    return place.object ?? copy.defaultObject
  }

  const joined = words.join('_')
  const pluralJoined = [...words.slice(0, -1), plural(words[words.length - 1])].join('_')

  return copy.objects[joined] ?? copy.objects[pluralJoined] ?? words.join(' ')
}

/** The service name a user knows, from an MCP server key ("AIMDSSuiteMCP" → "AIMDS Suite"). */
export function serviceDisplayName(server: string): string {
  const cleaned = server
    .replace(/[-_]?mcp[-_]?/gi, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .replace(/[-_]+/g, ' ')
    .trim()

  return cleaned || server
}

function mcpLabel(toolName: string, args: Record<string, unknown>, copy: ToolActivityCopy): ToolLabel {
  const server = stringField(args, 'mcp_server')
  const ownName = stringField(args, 'mcp_tool')
  const words = tokens(ownName || toolName.replace(/^mcp_/, '')).filter(word => word !== 'mcp')
  const areaIndex = words.findIndex(word => AREA_TOKENS[word])
  const areaKey = areaIndex >= 0 ? AREA_TOKENS[words[areaIndex]] : undefined

  const place =
    (areaKey && copy.areas[areaKey]) ||
    copy.customPlace(serviceDisplayName(server || (ownName ? '' : toolName.split('_')[1] || '')) || toolName)

  const verbIndex = words.findIndex(word => VERB_TOKENS[word])
  const verb: VerbKey = verbIndex >= 0 ? VERB_TOKENS[words[verbIndex]] : 'other'

  const objectWords =
    verbIndex >= 0 ? words.slice(verbIndex + 1).filter(word => !AREA_TOKENS[word] && !VERB_TOKENS[word]) : []

  const object = objectLabel(objectWords, place, copy)
  const template = copy.verbs[verb]

  return {
    area: place.name,
    done: template.done(object, place),
    kind: 'mcp',
    pending: template.pending(object, place),
    place
  }
}

/** Readable label for a tool call, or null when the tool keeps its own title. */
export function toolLabel(toolName: string, rawArgs?: unknown, copyOverride?: ToolActivityCopy): null | ToolLabel {
  const copy = activityCopy(copyOverride)
  const args = record(rawArgs)
  const name = String(toolName || '')

  if (PREPARE_TOOLS.has(name) || name === 'memory_context' || name.endsWith('_memory_context')) {
    return { ...copy.prepare, kind: 'internal' }
  }

  if (ANALYZE_TOOLS.has(name)) {
    return { ...copy.analyze, kind: 'internal' }
  }

  if (name.startsWith('mcp_') || args.mcp_server || args.mcp_tool) {
    return mcpLabel(name, args, copy)
  }

  const step = copy.steps[name]

  if (step) {
    const areaKey = STEP_AREAS[name]
    const place = areaKey ? copy.areas[areaKey] : undefined

    return { ...step, area: place?.name, kind: 'builtin', place }
  }

  return null
}

function compact(value: string, max: number): string {
  const flat = value.replace(/\s+/g, ' ').trim()

  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat
}

/** The one argument worth showing next to the label ("Urlaub 2026", "IAMDS-477").
 *  The gateway's `context` preview is used for MCP tools only: for built-ins
 *  it can be an English summary ("planning 3 task(s)") rather than an argument. */
export function toolDetail(rawArgs?: unknown, useContext = true): string {
  const args = record(rawArgs)
  let detail = useContext ? stringField(args, 'context') || stringField(args, 'preview') : ''

  if (!detail) {
    for (const key of DETAIL_KEYS) {
      detail = stringField(args, key)

      if (detail) {
        break
      }
    }
  }

  if (!detail) {
    return ''
  }

  // Paths: the file name says enough; the full path is in the expanded row.
  if (/[\\/]/.test(detail) && !/^https?:\/\//i.test(detail)) {
    detail = detail.split(/[\\/]/).filter(Boolean).pop() || detail
  }

  return compact(detail, 48)
}

/** Full title for a tool row: label plus its detail, localized. */
export function toolTitle(
  toolName: string,
  rawArgs: unknown,
  pending: boolean,
  copyOverride?: ToolActivityCopy
): null | string {
  const label = toolLabel(toolName, rawArgs, copyOverride)

  if (!label) {
    return null
  }

  const copy = activityCopy(copyOverride)
  const base = pending ? label.pending : label.done
  const detail = label.kind === 'internal' ? '' : toolDetail(rawArgs, label.kind === 'mcp')

  return detail ? copy.withDetail(base, detail) : base
}
