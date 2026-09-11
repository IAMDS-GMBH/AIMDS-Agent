import type { CronJob } from '@/types/hermes'

// Status-pip color per cron job state. Single source for the sidebar section and
// the Cron page so the two never drift. (Animation/size live at the call site.)
export const STATE_DOT: Record<string, string> = {
  completed: 'bg-(--ui-text-quaternary)',
  disabled: 'bg-(--ui-text-quaternary)',
  enabled: 'bg-primary',
  error: 'bg-destructive',
  paused: 'bg-amber-500',
  running: 'bg-primary',
  scheduled: 'bg-primary'
}

// Effective state: explicit state wins; otherwise infer from the enabled flag.
export function jobState(job: CronJob): string {
  const state = typeof job.state === 'string' ? job.state.trim() : ''

  return state || (job.enabled === false ? 'disabled' : 'scheduled')
}

// Human label for a job: name → first 60 of prompt → first 60 of script → id.
// One source for the sidebar row and the Cron page so the two never drift.
export function jobTitle(job: CronJob): string {
  const pick = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  const clip = (v: string) => (v.length > 60 ? `${v.slice(0, 60)}…` : v)

  return pick(job.name) || clip(pick(job.prompt)) || clip(pick(job.script)) || job.id || 'Cron job'
}

function parseIso(value: unknown): number | null {
  if (typeof value !== 'string' || !value.trim()) {
    return null
  }

  const ms = Date.parse(value)

  return Number.isNaN(ms) ? null : ms
}

// When the job's newest artifact was written, as a Date (null when the job has
// never produced one or the timestamp is unparsable).
export function outputDate(job: CronJob): Date | null {
  const ms = parseIso(job.last_output_at)

  return ms === null ? null : new Date(ms)
}

// Unread rule (AIS-305): a successful run wrote an artifact the user hasn't
// opened yet. A failed run never counts as "new output" — the error surfaces
// through the state pip / last_error instead. Timestamps are compared as
// instants so a `seen` from a different timezone still clears the flag.
export function hasNewOutput(job: CronJob): boolean {
  if (job.last_status === 'error') {
    return false
  }

  const outputAt = parseIso(job.last_output_at)

  if (outputAt === null) {
    return false
  }

  const seenAt = parseIso(job.last_seen_at)

  return seenAt === null || seenAt < outputAt
}

const BRIEF_SEED_KEYS = new Set(['morning-brief', 'weekly-review'])
const BRIEF_TEXT_RE =
  /\b(morning[\s-]?brief(?:ing)?|daily[\s-]?brief(?:ing)?|weekly[\s-]?review|morgen[\s-]?briefing|tages[\s-]?briefing|wochen[\s-]?(?:review|rückblick))\b/i

// Shipped brief jobs (seeded) or user-made lookalikes. Seed provenance is the
// authoritative signal; the name/prompt heuristic covers jobs created by hand
// before the seed existed.
export function isBriefJob(job: CronJob): boolean {
  const seedKey = typeof job.origin?.seed_key === 'string' ? job.origin.seed_key.trim().toLowerCase() : ''

  if (seedKey && BRIEF_SEED_KEYS.has(seedKey)) {
    return true
  }

  const name = typeof job.name === 'string' ? job.name : ''
  const prompt = typeof job.prompt === 'string' ? job.prompt.slice(0, 400) : ''

  return BRIEF_TEXT_RE.test(name) || BRIEF_TEXT_RE.test(prompt)
}

// Sort key: newest output first, jobs without output last.
export function compareByOutputDesc(a: CronJob, b: CronJob): number {
  return (parseIso(b.last_output_at) ?? -Infinity) - (parseIso(a.last_output_at) ?? -Infinity)
}
