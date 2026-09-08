import { getCronJobLatestOutput } from '@/hermes'
import { translateNow } from '@/i18n'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { markCronJobSeen } from '@/store/cron'
import { notifyError } from '@/store/notifications'
import { type PreviewTarget, setCurrentSessionPreviewTarget } from '@/store/preview'
import type { CronJob } from '@/types/hermes'

import { sessionRoute } from '../routes'

export interface OpenCronJobArtifactOptions {
  // Router navigate; when given, the run session opens after the artifact.
  navigate?: (route: string) => void
  // Explicit run to open (a row in the runs list); falls back to the job's
  // newest run session.
  runId?: null | string
  // Artifact path for that run when it differs from the job's newest output.
  path?: null | string
}

function trimmed(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

async function isLocallyReadable(path: string): Promise<boolean> {
  const desktop = window.hermesDesktop

  if (!desktop?.readFileText) {
    return false
  }

  try {
    await desktop.readFileText(path)

    return true
  } catch {
    return false
  }
}

function markdownTarget(path: string, text: string): PreviewTarget {
  const label = path.split(/[\\/]/).filter(Boolean).pop() || path

  return {
    kind: 'file',
    label,
    language: 'markdown',
    path,
    previewKind: 'text',
    source: path,
    text,
    url: `file://${path.startsWith('/') ? '' : '/'}${path.split('/').map(encodeURIComponent).join('/')}`
  }
}

// Resolve the artifact to a preview target: the local file when this machine
// can read it, else the backend's copy of the newest output (remote backend /
// path outside the sandbox). Returns null when neither works.
async function resolveArtifactTarget(job: CronJob, path: string): Promise<PreviewTarget | null> {
  if (path && (await isLocallyReadable(path))) {
    const target = await normalizeOrLocalPreviewTarget(path)

    if (target) {
      return target
    }
  }

  const latest = await getCronJobLatestOutput(job.id, job.profile)
  const content = typeof latest?.content === 'string' ? latest.content : ''
  const latestPath = trimmed(latest?.path) || path

  if (!latestPath && !content) {
    return null
  }

  return markdownTarget(latestPath || `${job.id}.md`, content)
}

// Open a cron job's artifact (AIS-305): file lane, markdown rendered, no
// persisted registry entry ('manual' source), then mark the job seen and —
// when a navigator is supplied — jump to the run session behind it. Errors
// surface as a toast; the promise resolves either way.
export async function openCronJobArtifact(job: CronJob, opts: OpenCronJobArtifactOptions = {}): Promise<boolean> {
  const path = trimmed(opts.path) || trimmed(job.last_output_path)

  try {
    const target = await resolveArtifactTarget(job, path)

    if (!target) {
      throw new Error(path || job.id)
    }

    setCurrentSessionPreviewTarget(target, 'manual', target.path || path || target.source)
  } catch (error) {
    notifyError(error, translateNow('cron.openOutputFailed'))

    return false
  }

  void markCronJobSeen(job.id)

  const runId = trimmed(opts.runId) || trimmed(job.last_run_session_id)

  if (opts.navigate && runId) {
    opts.navigate(sessionRoute(runId, job.profile))
  }

  return true
}
