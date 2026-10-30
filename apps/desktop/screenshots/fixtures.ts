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

// AIS-792: one answer that marks sections both ways models do — `## Heading`
// and a paragraph that is only a bold `**Label:**` — so the baseline shows
// they render as the same section label.
export const SECTIONS_SESSION_ID = 'screenshot-weekly-report'

const SECTIONS_ANSWER = [
  'Here is where the billing migration stands after this week.',
  '',
  '## Status',
  '',
  'Both pilot customers run on the new pipeline since Monday. The nightly export finished on time every night, and the month-end numbers match the old path to the cent.',
  '',
  '**Open:**',
  '',
  '- The reporting views still need a manual refresh after each customer.',
  '- Customer two asked for a dry run before their month-end close.',
  '',
  '**Next steps:**',
  '',
  '1. Schedule the dry run for Thursday morning.',
  '2. Add the refresh step to the migration checklist.',
  '3. Move the third customer once both pilots report a clean close.',
  '',
  '### Risks',
  '',
  'The only open risk is the export window: a migration batch must finish before 02:00, or the export reads a half-migrated customer. Note that **bold inside a sentence** stays as it is.'
].join('\n')

const sectionsMessages = [
  { content: 'Draft the weekly status report for the billing migration.', role: 'user', timestamp: NOW_S - 86_500 },
  { content: SECTIONS_ANSWER, role: 'assistant', timestamp: NOW_S - 86_400 }
]

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
    id: SECTIONS_SESSION_ID,
    input_tokens: 920,
    is_active: false,
    last_active: NOW_S - 86_400,
    message_count: 2,
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
  }
]

const longAnswerMessages = [
  {
    content: 'Compare the three rollout options for the new billing pipeline and recommend one.',
    role: 'user',
    timestamp: NOW_S - 900,
    // AIS-789: what the model understood, stored on the user message.
    understood:
      'You want the three rollout options for the new billing pipeline compared, with their trade-offs, and one of them recommended for a team of eight.'
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

export const FIXTURES: FakeDesktopFixtures = {
  api: {
    '/api/config': config,
    '/api/config/defaults': {},
    '/api/config/schema': { category_order: [], fields: {} },
    '/api/cron/jobs': [],
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
    [`/api/sessions/${LONG_ANSWER_SESSION_ID}/messages`]: {
      messages: longAnswerMessages,
      session_id: LONG_ANSWER_SESSION_ID
    },
    [`/api/sessions/${SECTIONS_SESSION_ID}/messages`]: {
      messages: sectionsMessages,
      session_id: SECTIONS_SESSION_ID
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
    },
    [`session.resume#${SECTIONS_SESSION_ID}`]: {
      info: { ...model, cwd: '/home/hermes', desktop_contract: 999 },
      message_count: sectionsMessages.length,
      messages: sectionsMessages,
      resumed: SECTIONS_SESSION_ID,
      running: false,
      session_id: 'runtime-sections'
    }
  }
}
