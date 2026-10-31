/**
 * Deterministic data for the screenshot check (AIS-798). Fixed ids, fixed
 * timestamps, no emoji (colour emoji differ per OS) — every pixel that changes
 * between runs must come from the UI, not the data.
 */

import type { FakeDesktopFixtures } from './fake-desktop'

/** The frozen "now" of every screenshot (Playwright clock). */
export const FROZEN_NOW = new Date('2026-10-30T09:30:00Z')

const NOW_S = Math.floor(FROZEN_NOW.getTime() / 1000)

export const LONG_ANSWER_SESSION_ID = 'screenshot-long-answer'

const LONG_ANSWER = [
  'Here is the comparison of the three rollout options, with the trade-offs that matter for a team of eight.',
  '',
  '## Summary',
  '',
  'Option B gives the best balance: it keeps the release cadence, needs no new infrastructure and can be rolled back per customer. Option A is cheaper up front but moves the risk to the first production week; option C is the most robust and also the slowest to land.',
  '',
  '| Option | Effort | Risk | Rollback | Ready by |',
  '| --- | --- | --- | --- | --- |',
  '| A: big-bang switch | 3 days | High | Full redeploy | Week 45 |',
  '| B: staged per customer | 6 days | Medium | Per customer | Week 46 |',
  '| C: parallel run | 11 days | Low | Instant | Week 48 |',
  '',
  '## Recommendation',
  '',
  '1. Start with **option B** for the two pilot customers next week.',
  '2. Keep the old path running until both pilots report a clean month-end close.',
  '3. Re-evaluate option C only if a pilot needs a rollback.',
  '',
  'The migration script itself is small:',
  '',
  '```python',
  'def migrate(customer: str, dry_run: bool = True) -> int:',
  '    rows = load_rows(customer)',
  '    for row in rows:',
  '        row.status = "migrated"',
  '    if not dry_run:',
  '        save_rows(customer, rows)',
  '    return len(rows)',
  '```',
  '',
  'Two things to watch: the nightly export runs at 02:00 and must not overlap with a migration batch, and the reporting views need a refresh after each customer. Both are covered by the checklist in the project notes.'
].join('\n')

const sessions = [
  {
    ended_at: null,
    id: LONG_ANSWER_SESSION_ID,
    input_tokens: 1840,
    is_active: false,
    last_active: NOW_S - 600,
    message_count: 2,
    model: 'suite-auto',
    output_tokens: 612,
    preview: 'Compare the three rollout options',
    source: 'desktop',
    started_at: NOW_S - 900,
    title: 'Rollout options compared',
    tool_call_count: 0
  },
  {
    ended_at: null,
    id: 'screenshot-weekly-report',
    input_tokens: 920,
    is_active: false,
    last_active: NOW_S - 86_400,
    message_count: 4,
    model: 'suite-auto',
    output_tokens: 310,
    preview: 'Draft the weekly status report',
    source: 'desktop',
    started_at: NOW_S - 90_000,
    title: 'Weekly status report',
    tool_call_count: 0
  },
  {
    ended_at: null,
    id: 'screenshot-invoice-check',
    input_tokens: 400,
    is_active: false,
    last_active: NOW_S - 3 * 86_400,
    message_count: 2,
    model: 'suite-auto',
    output_tokens: 150,
    preview: 'Check the October invoices',
    source: 'desktop',
    started_at: NOW_S - 3 * 86_400 - 600,
    title: 'October invoices',
    tool_call_count: 0
  },
  // AIS-794: one session per time group (Heute / Gestern / Diese Woche / Älter).
  {
    ended_at: null,
    id: 'screenshot-supplier-notes',
    input_tokens: 300,
    is_active: false,
    last_active: NOW_S - 12 * 86_400,
    message_count: 2,
    model: 'suite-auto',
    output_tokens: 120,
    preview: 'Summarise the supplier contract',
    source: 'desktop',
    started_at: NOW_S - 12 * 86_400 - 600,
    title: 'Supplier contract notes',
    tool_call_count: 0
  }
]

/** The pinned session ("Favoriten"). */
export const PINNED_SESSION_ID = 'screenshot-weekly-report'

// "Im Hintergrund": one automation with unseen output, one scheduled.
const cronJobs = [
  {
    enabled: true,
    id: 'screenshot-inbox-summary',
    last_output_at: '2026-10-30T07:00:00Z',
    last_run_at: '2026-10-30T07:00:00Z',
    last_status: 'ok',
    name: 'Inbox summary',
    next_run_at: '2026-10-31T07:00:00Z',
    state: 'scheduled'
  },
  {
    enabled: true,
    id: 'screenshot-invoice-reminder',
    name: 'Invoice reminder',
    next_run_at: '2026-10-30T13:00:00Z',
    state: 'scheduled'
  }
]

// Settings → Fähigkeiten.
const skills = [
  { category: 'productivity', description: 'Draft and tidy up meeting notes.', enabled: true, name: 'meeting-notes' },
  { category: 'productivity', description: 'Prepare a weekly status report.', enabled: true, name: 'weekly-report' },
  { category: 'research', description: 'Collect sources and summarise them.', enabled: false, name: 'deep-research' }
]

const toolsets = [
  {
    configured: true,
    description: 'Search the web and read pages.',
    enabled: true,
    label: 'Web',
    name: 'web',
    tools: ['web_search', 'web_extract']
  }
]

const longAnswerMessages = [
  {
    content: 'Compare the three rollout options for the new billing pipeline and recommend one.',
    role: 'user',
    timestamp: NOW_S - 900
  },
  { content: LONG_ANSWER, role: 'assistant', timestamp: NOW_S - 880 }
]

const config = {
  display: { language: 'en' },
  model: { default: 'suite-auto', provider: 'aimds-suite-prod' }
}

const profiles = [
  {
    has_env: true,
    is_default: true,
    model: 'suite-auto',
    name: 'default',
    path: '/home/hermes/.hermes',
    provider: 'aimds-suite-prod',
    skill_count: 0
  }
]

const status = {
  active_sessions: 0,
  config_path: '/home/hermes/.hermes/config.yaml',
  config_version: 1,
  env_path: '/home/hermes/.hermes/.env',
  gateway_exit_reason: null,
  gateway_health_url: null,
  gateway_pid: null,
  gateway_platforms: {},
  gateway_running: true,
  gateway_state: 'running',
  gateway_updated_at: null,
  hermes_home: '/home/hermes/.hermes',
  latest_config_version: 1,
  release_date: '2026-10-30',
  version: '0.7.15'
}

const sessionPage = {
  limit: 50,
  offset: 0,
  profile_totals: { default: sessions.length },
  sessions: sessions.map(session => ({ ...session, profile: 'default' })),
  total: sessions.length
}

const model = { model: 'suite-auto', provider: 'aimds-suite-prod' }

const providers = [
  {
    authenticated: true,
    is_current: true,
    models: ['suite-auto', 'suite-fast', 'suite-reasoning'],
    name: 'AIMDS Suite',
    slug: 'aimds-suite-prod',
    total_models: 3
  }
]

const auxiliaryTasks = ['vision', 'web_extract', 'compression', 'title_generation'].map(task => ({
  base_url: '',
  model: '',
  provider: '',
  task
}))

const emptySessionPage = { limit: 50, offset: 0, profile_totals: {}, sessions: [], total: 0 }

const namedProfile = (name: string, skillCount: number) => ({
  has_env: false,
  is_default: false,
  model: 'suite-auto',
  name,
  path: `/home/hermes/.hermes/profiles/${name}`,
  provider: 'aimds-suite-prod',
  skill_count: skillCount
})

/**
 * The profiles page (AIS-794: colour and order per profile) needs more than
 * one profile. Only that view gets them, so the other views keep the
 * single-profile shell.
 */
export const PROFILES_VIEW_API: Record<string, unknown> = {
  '/api/profiles': { profiles: [...profiles, namedProfile('work', 4), namedProfile('private', 1)] },
  // SOUL.md of the selected profile.
  '/api/profiles/*': { content: 'You help the team with clear, short answers in German or English.' }
}

export const FIXTURES: FakeDesktopFixtures = {
  localStorage: { 'hermes.desktop.pinnedSessions': JSON.stringify([PINNED_SESSION_ID]) },
  api: {
    '/api/config': config,
    '/api/config/defaults': {},
    '/api/config/schema': { category_order: [], fields: {} },
    '/api/cron/jobs': cronJobs,
    '/api/env': {},
    '/api/logs': { file: 'gui', lines: [] },
    '/api/mcp/servers': { servers: [] },
    '/api/model/auxiliary': { main: model, tasks: auxiliaryTasks },
    '/api/model/info': { ...model, effective_context_length: 200_000 },
    '/api/model/options': { ...model, providers },
    '/api/profiles': { profiles },
    '/api/profiles/active': { active: 'default', current: 'default' },
    '/api/profiles/sessions': sessionPage,
    // The cron section lists only scheduler sessions: none here.
    '/api/profiles/sessions?source=cron': emptySessionPage,
    '/api/providers/oauth': { providers: [] },
    '/api/sessions': sessionPage,
    '/api/skills': skills,
    '/api/tools/toolsets': toolsets,
    [`/api/sessions/${LONG_ANSWER_SESSION_ID}/messages`]: {
      messages: longAnswerMessages,
      session_id: LONG_ANSWER_SESSION_ID
    },
    '/api/status': status
  },
  rpc: {
    'complete.path': { items: [] },
    'model.options': { ...model, providers },
    'setup.runtime_check': { ok: true },
    'setup.status': { provider_configured: true },
    'session.resume': {
      // A backend new enough for any desktop build (no version-skew notice).
      info: { ...model, cwd: '/home/hermes', desktop_contract: 999 },
      message_count: longAnswerMessages.length,
      messages: longAnswerMessages,
      resumed: LONG_ANSWER_SESSION_ID,
      running: false,
      session_id: 'runtime-long-answer'
    }
  }
}
