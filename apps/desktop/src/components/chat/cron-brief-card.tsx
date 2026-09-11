import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import { useInRouterContext, useNavigate } from 'react-router-dom'

import { hasNewOutput, isBriefJob, jobTitle, outputDate } from '@/app/cron/job-state'
import { openCronJobArtifact } from '@/app/cron/open-artifact'
import {
  type CronOutputSummary,
  hasSummaryContent,
  parseCronOutputSummary,
  summaryFromApi
} from '@/app/cron/output-summary'
import { sessionRoute } from '@/app/routes'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { $cronJobs, $cronJobsWithNewOutput } from '@/store/cron'
import type { CronJob } from '@/types/hermes'

function isToday(date: Date, now = new Date()): boolean {
  return (
    date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
  )
}

// The job the intro should surface: the newest unseen output of any job, else
// today's most recent brief (already read, but still the day's context).
export function pickBriefJob(jobs: CronJob[], unseen: CronJob[], now = new Date()): CronJob | null {
  if (unseen.length > 0) {
    return unseen[0]
  }

  let best: CronJob | null = null
  let bestMs = -Infinity

  for (const job of jobs) {
    if (!isBriefJob(job)) {
      continue
    }

    const date = outputDate(job)

    if (!date || !isToday(date, now)) {
      continue
    }

    if (date.valueOf() > bestMs) {
      best = job
      bestMs = date.valueOf()
    }
  }

  return best
}

function formatOutputDate(date: Date, locale: string): string {
  return date.toLocaleString(locale, { day: 'numeric', hour: 'numeric', minute: '2-digit', month: 'short' })
}

// Backend summary first; when it carries nothing, read the artifact locally
// and parse the FINDING/NEXT lines ourselves (older scheduler builds only
// wrote the file).
function useBriefSummary(job: CronJob | null): CronOutputSummary {
  const apiSummary = useMemo(() => summaryFromApi(job?.last_output_summary), [job?.last_output_summary])
  const [parsed, setParsed] = useState<{ path: string; summary: CronOutputSummary } | null>(null)
  const path = job?.last_output_path ?? ''

  useEffect(() => {
    if (!path || hasSummaryContent(apiSummary) || !window.hermesDesktop?.readFileText) {
      return
    }

    let cancelled = false

    window.hermesDesktop
      .readFileText(path)
      .then(result => {
        if (!cancelled) {
          setParsed({ path, summary: parseCronOutputSummary(result?.text) })
        }
      })
      .catch(() => {
        // No local copy — the card just offers the open action.
      })

    return () => {
      cancelled = true
    }
  }, [apiSummary, path])

  if (hasSummaryContent(apiSummary)) {
    return apiSummary
  }

  return parsed && parsed.path === path ? parsed.summary : apiSummary
}

// The thread can mount outside a Router (secondary windows, tests); only take
// the navigator when one exists so the card still renders without deep links.
export function CronBriefCard() {
  return useInRouterContext() ? <RoutedCronBriefCard /> : <CronBriefCardBody />
}

function RoutedCronBriefCard() {
  const navigate = useNavigate()

  return <CronBriefCardBody navigate={route => navigate(route)} />
}

function CronBriefCardBody({ navigate }: { navigate?: (route: string) => void }) {
  const { locale, t } = useI18n()
  const jobs = useStore($cronJobs)
  const unseen = useStore($cronJobsWithNewOutput)
  const job = useMemo(() => pickBriefJob(jobs, unseen), [jobs, unseen])
  const summary = useBriefSummary(job)

  if (!job) {
    return null
  }

  const c = t.cron.brief
  const date = outputDate(job)
  const isNew = hasNewOutput(job)
  const runId = job.last_run_session_id?.trim() || ''
  const hasSummary = hasSummaryContent(summary)

  return (
    <section
      aria-label={c.title}
      className="w-full max-w-md rounded-md bg-(--ui-bg-quinary) px-4 py-3 text-left text-foreground"
      data-slot="cron_brief_card"
    >
      <div className="flex items-center gap-2">
        <Codicon className="shrink-0 text-(--theme-primary)" name="notebook" size="0.875rem" />
        <span className="min-w-0 truncate text-sm font-medium">{jobTitle(job)}</span>
        {isNew && <Badge>{c.badgeNew}</Badge>}
        {date && (
          <span className="ml-auto shrink-0 text-[0.6875rem] text-muted-foreground tabular-nums">
            {formatOutputDate(date, locale)}
          </span>
        )}
      </div>

      {hasSummary ? (
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          {summary.finding && (
            <>
              <dt className="font-medium uppercase tracking-wide text-muted-foreground">{c.finding}</dt>
              <dd className="line-clamp-2 m-0">{summary.finding}</dd>
            </>
          )}
          {summary.next && (
            <>
              <dt className="font-medium uppercase tracking-wide text-muted-foreground">{c.next}</dt>
              <dd className="line-clamp-2 m-0">{summary.next}</dd>
            </>
          )}
        </dl>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">{c.noSummary}</p>
      )}

      <div className="mt-3 flex items-center gap-2">
        <Button onClick={() => void openCronJobArtifact(job, { navigate })} size="sm">
          {c.openBrief}
        </Button>
        {runId && navigate && (
          <Button onClick={() => navigate(sessionRoute(runId, job.profile))} size="sm" variant="text">
            {c.openRun}
          </Button>
        )}
      </div>
    </section>
  )
}
