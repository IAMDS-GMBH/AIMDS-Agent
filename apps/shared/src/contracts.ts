// Generated from agent/contracts/*.schema.json by scripts/gen_contract_types.py.
// Do not edit by hand: change the schema and run the script.

export const CONTRACT_VERSIONS = {
  ledger_entry: 1,
  workspace_feed: 1,
  clarify_questions: 1,
  presence: 1,
  agent_envelope: 1,
  feedback_signature: 1
} as const

// ── ledger_entry ──

/** What the entry is about. */
export type LedgerKind = 'ticket' | 'mail' | 'chat' | 'file' | 'note' | 'web' | 'artifact' | 'decision' | 'open'

/** What the call did, normalised from the tool-name tokens. */
export type LedgerVerb = 'create' | 'update' | 'delete' | 'send' | 'write' | 'read' | 'list' | 'search' | 'ask'

/** Identifiers of the item an entry is about; at most one item per entry. */
export interface LedgerRef {
  id?: string
  /** Human key such as a ticket key. */
  key?: string
  message_id?: string
  path?: string
  /** Title or subject, clipped. */
  title?: string
  url?: string
}

/** Response of GET /api/sessions/{id}/ledger and payload of the session.ledger gateway event (delta). */
export interface LedgerPage {
  /** Oldest first. */
  entries: LedgerEntry[]
  lineage_root: string
  /** Session the request or event named. */
  session_id: string
  /** Entries the ledger holds for the lineage. */
  total: number
  v: 1
}

/** One thing a session did, derived deterministically from a tool call (AIS-797). Identifiers and short titles only, never bodies or mail text. */
export interface LedgerEntry {
  /** Most specific service behind the call, e.g. the upstream server a Suite tool proxies. */
  area: string
  /** Number of records a list or search returned. */
  count?: number
  /** Short answer of a decision (clarify), clipped. */
  detail?: string
  kind: LedgerKind
  /** Root session of the compression lineage; the ledger key. */
  lineage_root: string
  ref: LedgerRef
  /** Monotonic within the ledger; pass the last one as `after` to read only newer entries. */
  seq: number
  /** Session segment the call ran in. */
  session_id: string
  /** MCP server or toolset that served the call. */
  source: string
  /** Status of the referenced item as seen after the call. */
  status?: string
  /** Last status the ledger knew before a write changed it. */
  status_from?: string
  /** Status a write moved the item to. */
  status_to?: string
  /** Registered tool name. */
  tool: string
  /** Tool call id; matches tool_call_id of the tool message in the transcript. */
  tool_call_id?: string
  /** Unix epoch seconds (same clock as message timestamps). */
  ts: number
  /** Turn the call belongs to. */
  turn_id?: string
  /** Contract version. */
  v: 1
  verb: LedgerVerb
}

// ── workspace_feed ──

export interface FeedWindow {
  /** ISO 8601 */
  end: string
  kind: 'workspace'
  /** Next working day, YYYY-MM-DD. */
  preview_day: string
  /** ISO 8601 */
  start: string
  /** YYYY-MM-DD */
  today: string
}

export type FeedItemKind = 'event' | 'mail' | 'task' | 'ticket' | 'chat' | 'worklog' | 'note'

/** One normalised item (cron.brief_sources.base.BriefItem). */
export interface FeedItem {
  due_at?: string
  ends_at?: string
  /** Adapter-specific display fields (sender name, location, hours); not part of the stable contract. */
  extra?: Record<string, unknown>
  first_seen_at?: string | null
  /** source:kind:key */
  id: string
  key: string
  kind: FeedItemKind
  mine: boolean
  /** Status before the last change the store saw. */
  prev_status?: string | null
  priority?: string
  /** Adapter name: m365, jira, openproject, tempo, workspace. */
  source: string
  starts_at?: string
  status: string
  /** ISO 8601 time of that change. */
  status_changed_at?: string | null
  title: string
  updated_at?: string
  url?: string
}

/** Per-source outcome (cron.brief_sources.base.SourceStatus). */
export interface FeedSourceStatus {
  count: number
  detail: string
  /** Service name as the user knows it. */
  label: string
  name: string
  /** Active, but some sub-sources failed. */
  partial: boolean
  status: 'active' | 'skipped' | 'failed'
  /** Set up by the user but not usable right now — the only skip worth showing. */
  unavailable: boolean
}

/** The user's work across every active source (AIS-797): response of GET /api/workspace/feed. Collected LLM-free by the brief source adapters. */
export interface WorkspaceFeed {
  /** Seconds since generated_at. */
  age_s?: number
  /** Served from the cache instead of a fresh read. */
  cached?: boolean
  /** ISO 8601 time the sources were read. */
  generated_at: string
  items: FeedItem[]
  sources: FeedSourceStatus[]
  /** Contract version. */
  v: 1
  window: FeedWindow
}

// ── clarify_questions ──

export interface ClarifyQuestion {
  choices?: string[]
  question: string
  /** Short label that groups questions in the card. */
  topic?: string
}

/** Same values as the clarify tool's response_state. */
export type ClarifyAnswerState = 'answered' | 'skipped' | 'timeout' | 'empty' | 'delivery_failed' | 'unavailable'

export interface ClarifyAnswer {
  response: string
  state: ClarifyAnswerState
}

/** Questions the agent asks in one card and the answers it gets back (forward-looking; the question card implements it, AIS-793). */
export interface ClarifyQuestions {
  /** One per question, same order. */
  answers?: ClarifyAnswer[]
  questions: ClarifyQuestion[]
  /** Contract version. */
  v: 1
}

// ── presence ──

/** Where the agent runs: on the user's device or in the Suite. */
export type Runner = 'local' | 'suite'

/** A running agent announcing itself to the agent bus: where it runs and what it can do until it expires. */
export interface Presence {
  /** Capability names, e.g. toolsets or MCP servers it can serve. */
  capabilities: string[]
  /** Stable device id. */
  device: string
  /** ISO 8601 time after which the presence is stale. */
  expires: string
  /** Operating posture of the surface. */
  posture: 'general' | 'coding' | 'developer'
  runner: Runner
  /** ISO 8601 time of the announcement. */
  ts: string
  /** Contract version. */
  v: 1
  /** Agent version. */
  version: string
}

// ── agent_envelope ──

/** Pointer to an item by identifier; at least one of key, id or url. */
export interface AgentRef {
  id?: string
  key?: string
  /** Service the identifier belongs to. */
  source: string
  url?: string
}

/** A message between agents on the agent bus. Refs carry identifiers only, never content: the receiver reads the item itself with its own access. */
export interface AgentEnvelope {
  /** ISO 8601 time after which the envelope is dropped. */
  expires: string
  /** Sender device or agent id. */
  from: string
  /** Envelope id; reply_to of an answer names it. */
  id: string
  /** What the sender wants, in one sentence. */
  intent: string
  kind: 'notify' | 'handoff' | 'ask' | 'result'
  refs: AgentRef[]
  reply_to?: string
  /** Where the receiver should run the work. */
  runner: 'local' | 'suite'
  /** Receiver device or agent id. */
  to: string
  /** Contract version. */
  v: 1
}

// ── feedback_signature ──

export type FeedbackSignal = 'error' | 'timeout' | 'oversized' | 'missing_capability' | 'repeated_workaround' | 'parent_redo'

/** A recurring tool problem, counted for automatic quality reports. Signatures only: no arguments, results or other content. */
export interface FeedbackSignature {
  count: number
  /** MCP server or toolset. */
  server: string
  signal: FeedbackSignal
  tool: string
  /** Contract version. */
  v: 1
  /** Agent versions that saw the signal. */
  versions: string[]
}
