import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { DesktopUninstallSummary } from '@/global'
import { useI18n } from '@/i18n'
import { AlertTriangle, Loader2 } from '@/lib/icons'

import { SectionHeading } from './primitives'

export function UninstallSection() {
  const { t } = useI18n()
  const i18n = t.settings.about.uninstall

  const copy = i18n ?? {
    dangerZone: 'Danger zone',
    checkingInstalled: "Checking what's installed…",
    confirmTitle: 'Confirm uninstall',
    confirmBody:
      "This removes EVERYTHING — the Chat GUI, Hermes agent, and all local Hermes data. This can't be undone.",
    appPathLabel: 'App',
    runCta: 'Yes, uninstall',
    runningCta: 'Uninstalling…',
    cancel: 'Cancel',
    sectionTitle: 'Uninstall Hermes',
    sectionBody: 'This permanently removes the app, agent, and all local Hermes data. The app closes to finish the job.',
    uninstallAllCta: 'Uninstall everything',
    startFailed: 'Uninstall could not start.'
  }

  const [summary, setSummary] = useState<DesktopUninstallSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const bridge = window.hermesDesktop?.uninstall

    if (!bridge) {
      setLoading(false)

      return
    }

    void bridge
      .summary()
      .then(result => {
        if (alive) {
          setSummary(result)
        }
      })
      .catch(() => {
        // Non-fatal — we degrade to offering the GUI-only option.
      })
      .finally(() => {
        if (alive) {
          setLoading(false)
        }
      })

    return () => {
      alive = false
    }
  }, [])

  const bridge = window.hermesDesktop?.uninstall

  if (!bridge) {
    return null
  }

  const handleConfirm = async () => {
    if (!pending) {
      return
    }

    setRunning(true)
    setError(null)

    try {
      const result = await bridge.run('full')

      if (!result.ok) {
        setError(result.message || result.error || copy.startFailed)
        setRunning(false)
        setPending(false)
      }
      // On success the app quits shortly; keep the spinner up until it does.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setRunning(false)
      setPending(false)
    }
  }

  return (
    <div className="mx-auto mt-8 w-full max-w-2xl">
      <SectionHeading icon={AlertTriangle} title={copy.dangerZone} />

      <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3">
        {loading ? (
          <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {copy.checkingInstalled}
          </div>
        ) : pending ? (
          <div>
            <p className="text-sm font-medium text-destructive">{copy.confirmTitle}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {copy.confirmBody}
            </p>
            {summary?.running_app_path && (
              <p className="mt-1 font-mono text-[0.68rem] text-muted-foreground/60">
                {copy.appPathLabel}: {summary.running_app_path}
              </p>
            )}
            {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                disabled={running}
                onClick={() => void handleConfirm()}
                size="sm"
                variant="destructive"
              >
                {running && <Loader2 className="size-3 animate-spin" />}
                {running ? copy.runningCta : copy.runCta}
              </Button>
              <Button disabled={running} onClick={() => setPending(false)} size="sm" variant="text">
                {copy.cancel}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium">{copy.sectionTitle}</p>
            <p className="text-xs text-muted-foreground">
              {copy.sectionBody}
            </p>
            <div className="mt-2">
              <Button
                onClick={() => {
                  setError(null)
                  setPending(true)
                }}
                size="sm"
                variant="destructive"
              >
                {copy.uninstallAllCta}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
