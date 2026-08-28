import { atom } from 'nanostores'

import { notify } from '@/store/notifications'
import { addSupportTicket } from '@/store/support-tickets'

const ENABLED_KEY = 'hermes:feedback-prompts-enabled'
const LAST_SHOWN_KEY = 'hermes:feedback-prompts-last-shown'
const LAST_VERSION_KEY = 'hermes:feedback-prompts-last-version'

// 3 days interval for periodic prompts
const PROMPT_INTERVAL_MS = 3 * 24 * 60 * 60 * 1000

export const $feedbackPromptsEnabled = atom<boolean>(
  typeof localStorage !== 'undefined' ? localStorage.getItem(ENABLED_KEY) !== 'false' : true
)

export const $disableFeedbackPromptsDialogOpen = atom<boolean>(false)

export function openDisableFeedbackPromptsDialog() {
  $disableFeedbackPromptsDialogOpen.set(true)
}

export function closeDisableFeedbackPromptsDialog() {
  $disableFeedbackPromptsDialogOpen.set(false)
}

/**
 * Called on app startup or version refresh.
 * If version changed and prompts were previously disabled, re-enable them automatically
 * and display an informative notice to the user.
 */
export function initFeedbackPrompts(currentVersion?: string | null) {
  if (!currentVersion || typeof localStorage === 'undefined') {return}

  const lastVersion = localStorage.getItem(LAST_VERSION_KEY)
  const isEnabled = localStorage.getItem(ENABLED_KEY) !== 'false'

  if (lastVersion && lastVersion !== currentVersion) {
    if (!isEnabled) {
      localStorage.setItem(ENABLED_KEY, 'true')
      $feedbackPromptsEnabled.set(true)

      notify({
        id: 'feedback-prompts-reenabled-notice',
        kind: 'info',
        title: 'Feedback-Hinweise wieder aktiviert',
        message: `Die regelmäßigen Feedback-Hinweise wurden nach dem Update auf Version v${currentVersion} automatisch wieder aktiviert.`,
        durationMs: 9000
      })
    }
  }

  localStorage.setItem(LAST_VERSION_KEY, currentVersion)
}

/**
 * Checks if it's time to trigger a periodic feedback toast message.
 */
export function checkAndTriggerFeedbackPrompt() {
  if (typeof localStorage === 'undefined') {return}
  const isEnabled = $feedbackPromptsEnabled.get()

  if (!isEnabled) {return}

  const lastShownStr = localStorage.getItem(LAST_SHOWN_KEY)
  const lastShown = lastShownStr ? Number(lastShownStr) : 0
  const now = Date.now()

  // Delay first prompt slightly on fresh installs (e.g. set lastShown to now if never set)
  if (lastShown === 0) {
    localStorage.setItem(LAST_SHOWN_KEY, String(now))

    return
  }

  if (now - lastShown >= PROMPT_INTERVAL_MS) {
    localStorage.setItem(LAST_SHOWN_KEY, String(now))

    notify({
      id: 'feedback-prompt-toast',
      kind: 'info',
      title: 'Ihre Meinung zu Hermes',
      message: 'Haben Sie Feedback oder einen Verbesserungswunsch? Wir freuen uns über Ihre Rückmeldung!',
      durationMs: 12000,
      action: {
        label: 'Feedback senden',
        onClick: () => {
          if (typeof window !== 'undefined') {
            window.dispatchEvent(
              new CustomEvent('hermes:open-report-issue', {
                detail: { category: 'feature_request', summary: 'Feedback zu Hermes Agent' }
              })
            )
          }
        }
      }
    })
  }
}

/**
 * Disables feedback prompts after sending mandatory reason to support.
 */
export async function disableFeedbackPromptsWithReason(reason: string): Promise<{ ok: boolean; error?: string }> {
  const trimmedReason = reason.trim()

  if (!trimmedReason) {
    return { ok: false, error: 'Bitte geben Sie eine Begründung für die Deaktivierung an.' }
  }

  try {
    const desktop = window.hermesDesktop
    const fn = desktop?.reportIssue || desktop?.sendSupportLogs

    if (!fn) {
      throw new Error('Support-API nicht verfügbar.')
    }

    const summary = 'Feedback-Hinweise deaktiviert'
    const userDescription = `Benutzer hat regelmäßige Feedback-Hinweise deaktiviert.\n\nBegründung: ${trimmedReason}`

    const res = await fn({
      category: 'other',
      severity: 'low',
      summary,
      userDescription,
      clientType: 'hermes-desktop',
      contextType: 'manual',
      reason: 'feedback_prompts_disabled'
    } as any)

    if (res.ok) {
      const refId = res.reference_id || res.referenceId || 'SUP-DISABLED-PROMPTS'
      addSupportTicket({
        jobId: (res as any).job_id || (res as any).jobId || refId,
        caseId: (res as any).support_case_id || refId,
        referenceId: refId,
        summary,
        category: 'other',
        severity: 'low',
        createdAt: Date.now()
      })

      localStorage.setItem(ENABLED_KEY, 'false')
      $feedbackPromptsEnabled.set(false)

      notify({
        kind: 'success',
        message: 'Feedback-Hinweise wurden deaktiviert und Begründung an den Support gesendet.',
        durationMs: 5000
      })

      return { ok: true }
    } else {
      return { ok: false, error: res.error || 'Support-Server hat die Übermittlung abgelehnt.' }
    }
  } catch (err: any) {
    return { ok: false, error: err?.message || 'Unerwarteter Fehler beim Senden.' }
  }
}

/**
 * Manually re-enables feedback prompts.
 */
export function enableFeedbackPrompts() {
  localStorage.setItem(ENABLED_KEY, 'true')
  $feedbackPromptsEnabled.set(true)
  notify({
    kind: 'success',
    message: 'Feedback-Hinweise wurden wieder aktiviert.',
    durationMs: 4000
  })
}
