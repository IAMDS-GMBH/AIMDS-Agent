import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { AlertCircle, HelpCircle } from '@/lib/icons'
import {
  $disableFeedbackPromptsDialogOpen,
  closeDisableFeedbackPromptsDialog,
  disableFeedbackPromptsWithReason
} from '@/store/feedback-prompts'

export interface DisableFeedbackPromptsDialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function DisableFeedbackPromptsDialog({
  open: externalOpen,
  onOpenChange: externalOnOpenChange
}: DisableFeedbackPromptsDialogProps = {}) {
  const { t } = useI18n()
  const storeOpen = useStore($disableFeedbackPromptsDialogOpen)
  const open = externalOpen ?? storeOpen

  const handleOpenChange = (nextOpen: boolean) => {
    if (externalOnOpenChange) {
      externalOnOpenChange(nextOpen)
    } else if (!nextOpen) {
      closeDisableFeedbackPromptsDialog()
    }
  }

  const copy = t.feedbackPrompts || {
    disableDialogTitle: 'Feedback-Hinweise deaktivieren',
    disableDialogDesc:
      'Bitte geben Sie eine Begründung an, warum Sie keine regelmäßigen Feedback-Hinweise mehr erhalten möchten. Diese wird an den Support gesendet.',
    reasonLabel: 'Begründung (erforderlich)',
    reasonPlaceholder: 'Warum möchten Sie keine Feedback-Erinnerungen mehr erhalten?',
    reasonRequired: 'Eine Begründung ist erforderlich, um die Hinweise zu deaktivieren.',
    submitDisable: 'Deaktivieren & Absenden',
    submitting: 'Wird übermittelt…',
    cancel: 'Abbrechen'
  }

  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = reason.trim()

    if (!trimmed) {
      setError(copy.reasonRequired)

      return
    }

    setLoading(true)
    setError(null)

    const res = await disableFeedbackPromptsWithReason(trimmed)
    setLoading(false)

    if (res.ok) {
      setReason('')
      handleOpenChange(false)
    } else {
      setError(res.error || 'Fehler beim Senden.')
    }
  }

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent className="max-w-md gap-4 p-5">
        <DialogHeader>
          <DialogTitle icon={HelpCircle}>{copy.disableDialogTitle}</DialogTitle>
          <DialogDescription>{copy.disableDialogDesc}</DialogDescription>
        </DialogHeader>

        <form className="flex flex-col gap-3.5" onSubmit={handleSubmit}>
          {error && (
            <div className="flex items-start gap-2.5 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertCircle className="size-4 shrink-0" />
              <div>{error}</div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-foreground">{copy.reasonLabel}</label>
            <Textarea
              className="min-h-[90px] text-xs resize-y"
              onChange={e => {
                setReason(e.target.value)

                if (error) {setError(null)}
              }}
              placeholder={copy.reasonPlaceholder}
              required
              value={reason}
            />
          </div>

          <DialogFooter className="mt-2 pt-2 border-t border-border">
            <Button disabled={loading} onClick={() => handleOpenChange(false)} type="button" variant="outline">
              {copy.cancel}
            </Button>
            <Button disabled={loading || !reason.trim()} type="submit" variant="destructive">
              {loading ? copy.submitting : copy.submitDisable}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
