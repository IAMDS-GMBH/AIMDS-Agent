import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $notifications, clearNotifications } from '@/store/notifications'
import { $supportTickets } from '@/store/support-tickets'

import {
  $feedbackPromptsEnabled,
  checkAndTriggerFeedbackPrompt,
  disableFeedbackPromptsWithReason,
  enableFeedbackPrompts,
  initFeedbackPrompts
} from './feedback-prompts'

describe('feedback-prompts store', () => {
  beforeEach(() => {
    localStorage.clear()
    clearNotifications()
    $supportTickets.set([])
    $feedbackPromptsEnabled.set(true)
    vi.restoreAllMocks()
  })

  it('initFeedbackPrompts re-enables disabled prompts on version change', () => {
    localStorage.setItem('hermes:feedback-prompts-last-version', '1.0.0')
    localStorage.setItem('hermes:feedback-prompts-enabled', 'false')
    $feedbackPromptsEnabled.set(false)

    initFeedbackPrompts('1.1.0')

    expect(localStorage.getItem('hermes:feedback-prompts-enabled')).toBe('true')
    expect($feedbackPromptsEnabled.get()).toBe(true)
    expect(localStorage.getItem('hermes:feedback-prompts-last-version')).toBe('1.1.0')

    const notifications = $notifications.get()
    expect(notifications.length).toBe(1)
    expect(notifications[0].title).toBe('Feedback-Hinweise wieder aktiviert')
  })

  it('checkAndTriggerFeedbackPrompt does nothing if disabled', () => {
    $feedbackPromptsEnabled.set(false)
    localStorage.setItem('hermes:feedback-prompts-enabled', 'false')

    checkAndTriggerFeedbackPrompt()
    expect($notifications.get().length).toBe(0)
  })

  it('checkAndTriggerFeedbackPrompt triggers toast if interval passed', () => {
    const fourDaysAgo = Date.now() - 4 * 24 * 60 * 60 * 1000
    localStorage.setItem('hermes:feedback-prompts-last-shown', String(fourDaysAgo))
    $feedbackPromptsEnabled.set(true)

    checkAndTriggerFeedbackPrompt()

    const notifications = $notifications.get()
    expect(notifications.length).toBe(1)
    expect(notifications[0].title).toBe('Ihre Meinung zu Hermes')
  })

  it('disableFeedbackPromptsWithReason requires a non-empty reason', async () => {
    const res = await disableFeedbackPromptsWithReason('   ')
    expect(res.ok).toBe(false)
    expect(res.error).toBe('Bitte geben Sie eine Begründung für die Deaktivierung an.')
  })

  it('disableFeedbackPromptsWithReason sends support log and disables prompts', async () => {
    const reportIssueSpy = vi.fn().mockResolvedValue({
      ok: true,
      reference_id: 'SUP-12345',
      support_case_id: 'SUP-12345'
    })

    window.hermesDesktop = {
      reportIssue: reportIssueSpy
    } as any

    const res = await disableFeedbackPromptsWithReason('Kein Bedarf für Benachrichtigungen')
    expect(res.ok).toBe(true)
    expect(reportIssueSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        summary: 'Feedback-Hinweise deaktiviert',
        userDescription: expect.stringContaining('Kein Bedarf für Benachrichtigungen'),
        reason: 'feedback_prompts_disabled'
      })
    )

    expect($feedbackPromptsEnabled.get()).toBe(false)
    expect(localStorage.getItem('hermes:feedback-prompts-enabled')).toBe('false')
    expect($supportTickets.get().length).toBe(1)
  })

  it('enableFeedbackPrompts sets enabled to true', () => {
    $feedbackPromptsEnabled.set(false)
    localStorage.setItem('hermes:feedback-prompts-enabled', 'false')

    enableFeedbackPrompts()

    expect($feedbackPromptsEnabled.get()).toBe(true)
    expect(localStorage.getItem('hermes:feedback-prompts-enabled')).toBe('true')
  })
})
