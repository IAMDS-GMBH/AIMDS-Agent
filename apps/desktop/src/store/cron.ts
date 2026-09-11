import { atom, computed } from 'nanostores'

import { compareByOutputDesc, hasNewOutput } from '@/app/cron/job-state'
import { getCronJobs, markCronJobSeen as markCronJobSeenApi } from '@/hermes'
import type { CronJob } from '@/types/hermes'

// Cron *jobs* (not run sessions) power the sidebar "Cron jobs" section. Listing
// the job — schedule, state, live next-run countdown — makes the job the
// first-class entity; its runs (sessions) resolve under it in the cron detail.
export const $cronJobs = atom<CronJob[]>([])
export const setCronJobs = (jobs: CronJob[]) => $cronJobs.set(jobs)

// In-place edit so the cron overlay's mutations (create/edit/delete/pause/…)
// land in the same atom the sidebar renders — no stale list until the next poll.
export const updateCronJobs = (fn: (jobs: CronJob[]) => CronJob[]) => $cronJobs.set(fn($cronJobs.get()))

// One-shot focus target: clicking "Manage" on a job sets this, then opens the
// cron overlay, which reads it once to select + scroll to that job. Cleared
// after consumption so re-opening cron normally doesn't re-focus a stale job.
export const $cronFocusJobId = atom<null | string>(null)
export const setCronFocusJobId = (id: null | string) => $cronFocusJobId.set(id)

// Jobs whose newest artifact the user hasn't opened yet, newest output first.
// Drives the sidebar "New" pills, the intro brief card and the dock badge.
export const $cronJobsWithNewOutput = computed($cronJobs, jobs => jobs.filter(hasNewOutput).sort(compareByOutputDesc))

export const $unseenCronOutputCount = computed($cronJobsWithNewOutput, jobs => jobs.length)

// Mark a job's output as seen: flip the atom first so the pill/badge clear on
// the click, then persist. A failed persist re-fetches the list so the UI
// converges on what the server actually holds instead of lying until the next
// poll. Resolves either way — callers already opened the artifact.
export async function markCronJobSeen(jobId: string): Promise<void> {
  const job = $cronJobs.get().find(row => row.id === jobId)

  if (!job || !hasNewOutput(job)) {
    return
  }

  const seenAt = new Date().toISOString()

  updateCronJobs(rows => rows.map(row => (row.id === jobId ? { ...row, last_seen_at: seenAt } : row)))

  try {
    const updated = await markCronJobSeenApi(jobId, job.profile)

    if (updated && typeof updated === 'object' && updated.id === jobId) {
      updateCronJobs(rows => rows.map(row => (row.id === jobId ? { ...row, ...updated } : row)))
    }
  } catch {
    try {
      setCronJobs(await getCronJobs())
    } catch {
      // Keep the optimistic state; the interval poll reconciles later.
    }
  }
}
