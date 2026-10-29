import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { ReportIssueDialog } from '@/components/report-issue-dialog'
import { Button } from '@/components/ui/button'
import { writeClipboardText } from '@/components/ui/copy-button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { ErrorIcon, ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import type { DesktopUpdateCommit, DesktopUpdateStage, DesktopUpdateStatus } from '@/global'
import { useI18n } from '@/i18n'
import type { CommitGroup } from '@/lib/commit-changelog'
import { AlertCircle, Check, CheckCircle2, Copy, Terminal } from '@/lib/icons'
import { PRODUCT_DISPLAY_NAME } from '@/lib/product'
import { resolveUpdateCopy, type UpdateTarget } from '@/lib/update-copy'
import { resolveUpdateHeader, resolveUpdateNotes } from '@/lib/update-notes'
import { cn } from '@/lib/utils'
import {
  $backendUpdateApply,
  $backendUpdateChecking,
  $backendUpdateStatus,
  $updateApply,
  $updateChecking,
  $updateOverlayOpen,
  $updateOverlayTarget,
  $updateStatus,
  applyBackendUpdate,
  applyUpdates,
  checkBackendUpdates,
  checkUpdates,
  resetUpdateApplyState,
  setUpdateOverlayOpen,
  type UpdateApplyState
} from '@/store/updates'

function totalItems(groups: readonly CommitGroup[]) {
  return groups.reduce((sum, g) => sum + g.items.length, 0)
}

export function UpdatesOverlay() {
  const open = useStore($updateOverlayOpen)
  const target = useStore($updateOverlayTarget)
  const [reportOpen, setReportOpen] = useState(false)
  const { t } = useI18n()
  const u = t.updates
  // AIS-755: pre-filled report summaries follow the UI language.
  const [reportSummary, setReportSummary] = useState<null | string>(null)

  const clientStatus = useStore($updateStatus)
  const clientChecking = useStore($updateChecking)
  const clientApply = useStore($updateApply)
  const backendStatus = useStore($backendUpdateStatus)
  const backendChecking = useStore($backendUpdateChecking)
  const backendApply = useStore($backendUpdateApply)

  const isBackend = target === 'backend'
  const status = isBackend ? backendStatus : clientStatus
  const checking = isBackend ? backendChecking : clientChecking
  const apply = isBackend ? backendApply : clientApply
  const check = isBackend ? checkBackendUpdates : checkUpdates
  const install = isBackend ? applyBackendUpdate : applyUpdates

  useEffect(() => {
    if (open && !status && !checking) {
      void check()
    }
  }, [check, checking, open, status])

  const behind = status?.behind ?? 0

  const phase: 'idle' | 'applying' | 'manual' | 'error' =
    apply.stage === 'manual'
      ? 'manual'
      : apply.applying || apply.stage === 'restart'
        ? 'applying'
        : apply.stage === 'error'
          ? 'error'
          : 'idle'

  const handleClose = (next: boolean) => {
    if (phase === 'applying') {
      return
    }

    setUpdateOverlayOpen(next)

    if (!next && (apply.stage === 'error' || apply.stage === 'restart' || apply.stage === 'manual')) {
      resetUpdateApplyState()
    }
  }

  const handleInstall = () => {
    void install()
  }

  const handleReportIssue = (summary?: string) => {
    if (summary) {setReportSummary(summary)}
    setReportOpen(true)
  }

  return (
    <>
      <Dialog onOpenChange={handleClose} open={open}>
        <DialogContent
          className="w-[calc(100vw-2rem)] max-w-xl gap-0 border-border/70 p-0"
          showCloseButton={phase !== 'applying'}
        >
          {phase === 'applying' && (
            <ApplyingView apply={apply} isBackend={isBackend} onReportIssue={() => handleReportIssue(u.reportSummary.stuck)} />
          )}

          {phase === 'manual' && (
            <ManualView command={apply.command ?? 'hermes update'} onDone={() => handleClose(false)} onReportIssue={() => handleReportIssue(u.reportSummary.manualFailed)} />
          )}

          {phase === 'error' && (
            <ErrorView message={apply.message} offline={apply.error === 'offline'} onDismiss={() => handleClose(false)} onReportIssue={() => handleReportIssue(u.reportSummary.failed(apply.message))} onRetry={handleInstall} />
          )}

          {phase === 'idle' && (
            <IdleView
              behind={behind}
              checking={checking}
              commits={status?.commits ?? []}
              onInstall={handleInstall}
              onLater={() => handleClose(false)}
              onReportIssue={handleReportIssue}
              onRetryCheck={() => void check()}
              status={status}
              target={target}
            />
          )}
        </DialogContent>
      </Dialog>
      <ReportIssueDialog
        contextType="update_error"
        defaultCategory="installation_update"
        defaultSummary={reportSummary ?? u.reportSummary.generic}
        installType="update"
        onOpenChange={setReportOpen}
        open={reportOpen}
      />
    </>
  )
}

// Exported for the AIS-276 regression test: a mounted IdleView must survive
// every status transition without changing its hook count.
export function IdleView({
  behind,
  checking,
  commits,
  onInstall,
  onLater,
  onReportIssue,
  onRetryCheck,
  status,
  target
}: {
  behind: number
  checking: boolean
  commits: readonly DesktopUpdateCommit[]
  onInstall: () => void
  onLater: () => void
  onReportIssue: (summary?: string) => void
  onRetryCheck: () => void
  status: DesktopUpdateStatus | null
  target: UpdateTarget
}) {
  // locale is destructured here too: a hook below the early returns violated
  // the Rules of Hooks — a mounted IdleView switching from the changelog path
  // to any status path rendered fewer hooks and crashed the root boundary
  // with React #300 (AIS-276).
  const { locale, t } = useI18n()
  const u = t.updates

  if (!status && checking) {
    return (
      <CenteredStatus icon={<Loader className="size-12" label={u.checking} type="lemniscate-bloom" />} title={u.checking} />
    )
  }

  if (!status) {
    return (
      <CenteredStatus
        action={
          <div className="flex items-center gap-2">
            <Button onClick={onRetryCheck} size="sm">
              {u.tryAgain}
            </Button>
            <Button onClick={() => onReportIssue(u.reportSummary.checkFailed)} size="sm" variant="outline">
              {t.reportIssue.title}
            </Button>
          </div>
        }
        icon={<ErrorIcon />}
        title={u.checkFailedTitle}
      />
    )
  }

  if (!status.supported) {
    return (
      <CenteredStatus
        body={status.message ?? u.unsupportedMessage}
        icon={<AlertCircle className="size-6 text-muted-foreground" />}
        title={u.notAvailableTitle}
      />
    )
  }

  if (status.error) {
    return (
      <CenteredStatus
        action={
          <div className="flex items-center gap-2">
            <Button disabled={checking} onClick={onRetryCheck} size="sm">
              {u.tryAgain}
            </Button>
            <Button onClick={() => onReportIssue(u.reportSummary.checkUnreachable)} size="sm" variant="outline">
              {t.reportIssue.title}
            </Button>
          </div>
        }
        body={u.connectionRetry}
        icon={<ErrorIcon />}
        title={u.checkFailedTitle}
      />
    )
  }

  // Tag channel, HEAD on a release tag *newer* than the channel's target
  // (v0.7.5-rc.1 while stable is still v0.7.4): up to date — no install
  // button, the older release is not an update (AIS-299).
  if (behind === 0 && status.newerThanTarget && status.headTag && status.targetTag) {
    return (
      <CenteredStatus
        body={u.newerReleaseBody(status.headTag, status.targetTag, status.branch ?? 'stable')}
        icon={<CheckCircle2 className="size-7 text-emerald-600 dark:text-emerald-400" />}
        title={u.allSetTitle}
      />
    )
  }

  // Tag channel, HEAD past the release tag (dev/main checkout on stable or
  // preview). Nothing to *update*, but the channel's release is installable —
  // offer that explicitly instead of the phantom "+1 update" that looped in
  // SUP-20260907-101225 (AIS-297).
  if (behind === 0 && status.offChannel && status.targetTag) {
    const tag = status.targetTag

    return (
      <CenteredStatus
        action={
          <div className="flex items-center gap-2">
            <Button onClick={onInstall} size="sm">
              {u.switchToRelease(tag)}
            </Button>
            <Button onClick={onLater} size="sm" variant="outline">
              {u.maybeLater}
            </Button>
          </div>
        }
        body={u.offChannelBody(status.aheadOfTarget ?? 0, tag, status.branch ?? 'stable')}
        icon={<AlertCircle className="size-6 text-muted-foreground" />}
        title={u.offChannelTitle(tag)}
      />
    )
  }

  if (behind === 0) {
    return (
      <CenteredStatus
        body={target === 'backend' ? u.latestBodyBackend : u.latestBody}
        icon={<CheckCircle2 className="size-7 text-emerald-600 dark:text-emerald-400" />}
        title={u.allSetTitle}
      />
    )
  }

  // AIS-750: one card for git and release-managed installs (AIS-312). The
  // header names the product and the target version; release channels list
  // the curated notes (or one neutral line), only the main channel shows the
  // scrubbed commit subjects. No hooks here (AIS-276).
  const header = resolveUpdateHeader(status, behind, u)
  const { commitItems, groups } = resolveUpdateNotes({ commits, locale, status })
  const remaining = header.developer && commitItems > 0 ? Math.max(0, behind - commitItems) : 0

  // Name what's being updated. In remote mode the overlay acts on the connected
  // backend, not the local client — say so.
  const { title, body } = resolveUpdateCopy({ target, shownItems: totalItems(groups), copy: u })

  return (
    <div className="grid gap-5 px-5 pb-6 pt-7 pr-8 sm:px-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <BrandMark className="size-16" />

        <div className="flex flex-col items-center gap-0.5" data-testid="update-header">
          <p className="text-base font-semibold tracking-tight text-foreground">{PRODUCT_DISPLAY_NAME}</p>
          {header.versionLine && <p className="text-xs font-medium text-primary">{header.versionLine}</p>}
          {header.installedLine && <p className="text-[0.7rem] text-muted-foreground">{header.installedLine}</p>}
        </div>

        <div className="flex flex-col items-center gap-1">
          <DialogTitle className="text-center text-lg">{title}</DialogTitle>
          <DialogDescription className="text-center text-sm">{body}</DialogDescription>
        </div>
      </div>

      <div className="grid max-h-[min(20rem,40vh)] gap-3 overflow-y-auto overscroll-contain rounded-xl border border-border/70 bg-muted/20 px-4 py-3">
        {groups.map(group => (
          <div key={group.id}>
            <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-muted-foreground">{group.label}</p>
            <ul className="mt-1.5 grid gap-1.5 text-xs text-foreground">
              {group.items.map(item => (
                <li className="flex items-start gap-2" key={item}>
                  <span aria-hidden className="mt-1.5 inline-block size-1 shrink-0 rounded-full bg-primary" />
                  <span className="leading-snug">{item}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="grid gap-2">
        <Button className="font-semibold" onClick={onInstall} size="lg">
          {u.updateNow}
        </Button>
        <div className="flex items-center justify-between gap-2 px-1">
          <Button className="font-medium" onClick={onLater} type="button" variant="text">
            {u.maybeLater}
          </Button>
          <Button onClick={() => onReportIssue(u.reportSummary.generic)} size="xs" type="button" variant="text">
            {t.reportIssue.title}
          </Button>
        </div>
      </div>

      {remaining > 0 && (
        <p className="text-center text-xs text-muted-foreground">
          {u.moreChanges(remaining)}
        </p>
      )}
    </div>
  )
}

function ManualView({ command, onDone, onReportIssue }: { command: string; onDone: () => void; onReportIssue: () => void }) {
  const { t } = useI18n()
  const u = t.updates
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    void writeClipboardText(command).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    })
  }

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <Terminal className="size-8 text-primary" />

        <DialogTitle className="text-center text-xl">{u.manualTitle}</DialogTitle>
        <DialogDescription className="text-center text-sm">
          {u.manualBody}
        </DialogDescription>
      </div>

      <button
        className="group flex w-full items-center justify-between gap-3 rounded-xl border border-border/70 bg-muted/30 px-4 py-3 text-left transition-colors hover:border-border hover:bg-muted/50"
        onClick={handleCopy}
        type="button"
      >
        <code className="select-all font-mono text-sm text-foreground">
          <span className="text-muted-foreground">$ </span>
          {command}
        </code>
        <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-muted-foreground transition-colors group-hover:text-foreground">
          {copied ? (
            <>
              <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
              {u.copied}
            </>
          ) : (
            <>
              <Copy className="size-3.5" />
              {u.copy}
            </>
          )}
        </span>
      </button>

      <p className="text-center text-xs text-muted-foreground">
        {u.manualPickedUp}
      </p>

      <div className="grid gap-2">
        <Button className="font-semibold" onClick={onDone} size="lg" variant="secondary">
          {u.done}
        </Button>
        <Button onClick={onReportIssue} size="xs" type="button" variant="text">
          {t.reportIssue.title}
        </Button>
      </div>
    </div>
  )
}

function ApplyingView({ apply, isBackend, onReportIssue }: { apply: UpdateApplyState; isBackend: boolean; onReportIssue: () => void }) {
  const { t } = useI18n()
  const u = t.updates
  const label = u.stages[apply.stage as DesktopUpdateStage] ?? u.stages.idle
  const body = isBackend ? u.applyingBodyBackend : u.applyingBody

  const percent =
    typeof apply.percent === 'number' && Number.isFinite(apply.percent)
      ? Math.max(2, Math.min(100, Math.round(apply.percent)))
      : null

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7">
      <div className="flex flex-col items-center gap-3 text-center">
        <Loader className="size-16" label={label} type="lemniscate-bloom" />

        <DialogTitle className="text-center text-xl">{label}</DialogTitle>
        <DialogDescription className="text-center text-sm">
          {body}
        </DialogDescription>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full bg-primary transition-[width] duration-300 ease-out',
            percent === null && 'w-1/3 animate-pulse'
          )}
          style={percent !== null ? { width: `${percent}%` } : undefined}
        />
      </div>

      <div className="flex flex-col items-center gap-1">
        <p className="text-center text-xs text-muted-foreground">{u.applyingClose}</p>
        <Button onClick={onReportIssue} size="xs" type="button" variant="text">
          {t.reportIssue.title}
        </Button>
      </div>
    </div>
  )
}

function ErrorView({
  message,
  offline = false,
  onDismiss,
  onReportIssue,
  onRetry
}: {
  message: string
  offline?: boolean
  onDismiss: () => void
  onReportIssue: () => void
  onRetry: () => void
}) {
  const { t } = useI18n()
  const u = t.updates

  return (
    <ErrorState
      className="px-6 pb-6 pt-7 pr-8"
      description={
        <DialogDescription className="max-w-prose text-center text-sm leading-5 text-muted-foreground">
          {offline ? u.offlineBody : message || u.errorBody}
        </DialogDescription>
      }
      title={
        <DialogTitle className="text-center text-xl font-semibold tracking-tight">
          {offline ? u.offlineTitle : u.errorTitle}
        </DialogTitle>
      }
    >
      <Button className="font-semibold" onClick={onRetry} size="lg">
        {u.tryAgain}
      </Button>
      <Button onClick={onReportIssue} variant="outline">
        {t.reportIssue.title}
      </Button>
      <Button onClick={onDismiss} variant="text">
        {u.notNow}
      </Button>
    </ErrorState>
  )
}

function CenteredStatus({
  action,
  body,
  icon,
  title
}: {
  action?: React.ReactNode
  body?: string
  icon: React.ReactNode
  title: string
}) {
  return (
    <div className="grid gap-4 px-6 pb-6 pt-8 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        {icon}

        <DialogTitle className="text-center text-lg">{title}</DialogTitle>
        {body && <DialogDescription className="text-center text-sm">{body}</DialogDescription>}
      </div>

      {action && <div className="flex justify-center">{action}</div>}
    </div>
  )
}
