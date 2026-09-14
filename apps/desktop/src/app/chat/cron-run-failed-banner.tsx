import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { Button } from '@/components/ui/button'
import { triggerCronJob } from '@/hermes'
import { useI18n } from '@/i18n'
import { failedCronRunJob } from '@/lib/cron-run-state'
import { $cronJobs, updateCronJobs } from '@/store/cron'
import { notifyError } from '@/store/notifications'
import { $messages } from '@/store/session'

// A cron run that failed leaves a transcript with nothing but the prompt; the
// session then rendered as an empty chat with no trace of the failure — the
// morning brief "shows nothing" (AIS-332 / SUP-20260914-063903). The failure
// is already on the job record (`last_error`), so surface it where the user
// is looking and offer the run-now action from the tasks page.
export function CronRunFailedBanner({ sessionId }: { sessionId: null | string }) {
  const { t } = useI18n()
  const copy = t.cron.runFailed
  const jobs = useStore($cronJobs)
  const messages = useStore($messages)
  const [running, setRunning] = useState(false)

  const job = failedCronRunJob(sessionId, messages, jobs)

  const runNow = useCallback(async () => {
    if (!job || running) {
      return
    }

    setRunning(true)

    try {
      const updated = await triggerCronJob(job.id)
      updateCronJobs(current => current.map(candidate => (candidate.id === updated.id ? updated : candidate)))
    } catch (error) {
      notifyError(error, copy.runFailed)
    } finally {
      setRunning(false)
    }
  }, [copy.runFailed, job, running])

  if (!job) {
    return null
  }

  return (
    <div
      className="mx-auto mt-2 flex w-full max-w-3xl items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm"
      data-testid="cron-run-failed-banner"
      role="alert"
    >
      <div className="min-w-0 flex-1">
        <div className="font-medium">{copy.title}</div>
        {job.last_error && <div className="mt-0.5 break-words text-muted-foreground">{job.last_error}</div>}
      </div>
      <Button disabled={running} onClick={() => void runNow()} size="sm" variant="outline">
        {running ? copy.running : copy.runNow}
      </Button>
    </div>
  )
}
