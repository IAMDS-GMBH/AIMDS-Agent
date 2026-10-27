'use client'

import { useStore } from '@nanostores/react'
import { type FC, useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { ChevronDown, Loader2 } from '@/lib/icons'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'
import { $approvalRequest, type ApprovalRequest, clearApprovalRequest } from '@/store/prompts'

// Approval notice (AIS-746). The gateway blocks the agent thread on exactly one
// approval per session until approval.respond lands (5 min, then the tool is
// BLOCKED), so the request must be impossible to miss. It used to be an inline
// strip under the pending tool row, but since the activity block (AIS-744)
// that row can sit far up in a long turn — above later file-edit rows — and the
// chat looked stuck. The notice therefore renders once, at the end of the
// running assistant message, with a plain-language reason and the command
// behind a disclosure. `$approvalRequest` is already scoped to the active
// session; the event payload is the only place the command reliably exists.
export const APPROVAL_TOOLS = new Set([
  'terminal',
  'execute_code',
  'bash',
  'powershell',
  'cmd',
  'sh',
  'python',
  'code_execution',
  'edit_file',
  'write_file',
  'mcp',
  'mcp_tool'
])

/** Whether `toolName` is the running step that waits for the user's approval. */
export function useAwaitingApproval(toolName: string | undefined): boolean {
  const request = useStore($approvalRequest)

  return Boolean(request && toolName && APPROVAL_TOOLS.has(toolName))
}

// Canonical gateway choices (ui-tui/src/components/prompts.tsx).
type ApprovalChoice = 'once' | 'session' | 'always' | 'deny'

export const PendingApprovalNotice: FC = () => {
  const { t } = useI18n()
  const copy = t.assistant.approval
  const request = useStore($approvalRequest)

  if (!request) {
    return null
  }

  const command = request.command.trim()

  return (
    <div
      aria-live="assertive"
      className="mt-2 grid max-w-full gap-1.5 rounded-[0.625rem] border border-primary/30 bg-primary/5 px-3 py-2"
      data-slot="tool-approval-notice"
      role="alert"
    >
      <div className="text-sm font-medium text-foreground">{copy.waitingTitle}</div>
      <p className="text-xs leading-snug text-(--ui-text-secondary)">{copy.waitingBody}</p>
      {command && (
        <details className="text-xs text-(--ui-text-tertiary)">
          <summary className="cursor-pointer select-none">{copy.showCommand}</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-2.5 py-1.5 font-mono text-xs leading-snug text-foreground">
            {command}
          </pre>
        </details>
      )}
      <ApprovalBar request={request} />
    </div>
  )
}

const isMac = typeof navigator !== 'undefined' && /Mac|iP(hone|ad|od)/.test(navigator.platform)

const ApprovalBar: FC<{ request: ApprovalRequest }> = ({ request }) => {
  const { t } = useI18n()
  const copy = t.assistant.approval
  const gateway = useStore($gateway)
  const [submitting, setSubmitting] = useState<ApprovalChoice | null>(null)
  // "Always allow" persists the pattern to ~/.hermes/config.yaml permanently, so
  // it goes through a confirm step rather than firing straight from the menu.
  const [confirmAlways, setConfirmAlways] = useState(false)
  const busy = submitting !== null

  const respond = useCallback(
    async (choice: ApprovalChoice) => {
      // Another bar (or the keyboard path) may have already resolved this
      // approval; the atom is the single source of truth, so bail if it's gone.
      if (busy || !$approvalRequest.get()) {
        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed)

        return
      }

      setSubmitting(choice)

      try {
        await gateway.request<{ resolved?: boolean }>('approval.respond', {
          choice,
          session_id: request.sessionId ?? undefined
        })
        triggerHaptic(choice === 'deny' ? 'cancel' : 'submit')
        clearApprovalRequest(request.sessionId)
      } catch (error) {
        clearApprovalRequest(request.sessionId)
        const msg = error instanceof Error ? error.message : String(error)

        if (!msg.toLowerCase().includes('no pending')) {
          notifyError(error, copy.sendFailed)
        }

        setSubmitting(null)
      }
    },
    [busy, gateway, request.sessionId]
  )

  // ⌘/Ctrl+Enter → Run, Esc → Reject.
  // While the confirm dialog is open it owns the keyboard (Esc closes it), so
  // the strip-level shortcuts stand down to avoid denying the whole approval.
  useEffect(() => {
    if (confirmAlways) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        void respond('once')
      } else if (event.key === 'Escape') {
        event.preventDefault()
        void respond('deny')
      }
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [confirmAlways, respond])

  return (
    <div className="flex items-center gap-2.5" data-slot="tool-approval-inline">
      <div className="inline-flex h-6 items-stretch overflow-hidden rounded-md border border-primary/25 bg-primary/10 text-primary">
        <Button
          className="h-full gap-1 rounded-none px-2 text-xs font-medium text-primary hover:bg-primary/15 hover:text-primary"
          disabled={busy}
          onClick={() => void respond('once')}
          size="xs"
          variant="ghost"
        >
          {submitting === 'once' ? <Loader2 className="size-3 animate-spin" /> : copy.run}
          {submitting !== 'once' && <span className="text-[0.625rem] text-primary/60">{isMac ? '⌘⏎' : 'Ctrl⏎'}</span>}
        </Button>
        <span aria-hidden className="w-px self-stretch bg-primary/20" />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label={copy.moreOptions}
              className="h-full w-5 rounded-none px-0 text-primary hover:bg-primary/15 hover:text-primary"
              disabled={busy}
              size="xs"
              variant="ghost"
            >
              <ChevronDown className="size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-44">
            <DropdownMenuItem onSelect={() => void respond('session')}>{copy.allowSession}</DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                // Defer one tick so the menu fully unmounts before the dialog
                // mounts — otherwise Radix's focus-return races the dialog and
                // dismisses it via onInteractOutside.
                setTimeout(() => setConfirmAlways(true), 0)
              }}
            >
              {copy.alwaysAllowMenu}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void respond('deny')} variant="destructive">
              {copy.reject}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Button
        className="h-6 gap-1.5 rounded-md px-1.5 text-xs font-normal text-(--ui-text-tertiary) hover:text-foreground"
        disabled={busy}
        onClick={() => void respond('deny')}
        size="xs"
        variant="ghost"
      >
        {submitting === 'deny' ? <Loader2 className="size-3 animate-spin" /> : copy.reject}
        {submitting !== 'deny' && <span className="text-[0.625rem] opacity-55">Esc</span>}
      </Button>

      <Dialog onOpenChange={setConfirmAlways} open={confirmAlways}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{copy.alwaysTitle}</DialogTitle>
            <DialogDescription>
              {copy.alwaysDescription(request.description)}
            </DialogDescription>
          </DialogHeader>

          {request.command.trim() && (
            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-2.5 py-1.5 font-mono text-xs leading-snug text-foreground">
              {request.command.trim()}
            </pre>
          )}

          <DialogFooter>
            <Button onClick={() => setConfirmAlways(false)} size="sm" variant="ghost">
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => {
                setConfirmAlways(false)
                void respond('always')
              }}
              size="sm"
              variant="destructive"
            >
              {copy.alwaysAllow}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
