import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $supportTickets,
  addSupportTicket,
  canEditSupportTicket,
  editSupportTicket,
  type SavedSupportTicket,
  withdrawSupportTicket
} from './support-tickets'

function ticket(): SavedSupportTicket {
  return $supportTickets.get().find(t => t.jobId === 'job-1') as SavedSupportTicket
}

describe('support ticket self-service (AIS-399)', () => {
  beforeEach(() => {
    $supportTickets.set([])
    addSupportTicket({
      caseId: 'SUP-1',
      category: 'ui_bug',
      description: 'old',
      editToken: 'tok',
      jobId: 'job-1',
      severity: 'low',
      summary: 'Old summary',
      uploadUrl: 'https://s.example/api/v1/upload'
    })
  })

  afterEach(() => {
    window.hermesDesktop = undefined as unknown as typeof window.hermesDesktop
    window.localStorage.clear()
  })

  it('only offers editing for open tickets that carry an edit token', () => {
    expect(canEditSupportTicket(ticket())).toBe(true)
    expect(canEditSupportTicket({ ...ticket(), editToken: undefined })).toBe(false)
    expect(canEditSupportTicket({ ...ticket(), status: 'RESOLVED' })).toBe(false)
  })

  it('applies a successful edit to the local ticket', async () => {
    const editSupportCase = vi.fn().mockResolvedValue({ code: 200, ok: true, status: 'IN_PROGRESS' })
    window.hermesDesktop = { editSupportCase } as unknown as typeof window.hermesDesktop

    const result = await editSupportTicket(ticket(), {
      category: 'chat_issue',
      description: 'new details',
      severity: 'high',
      summary: 'New summary'
    })

    expect(result.ok).toBe(true)
    expect(editSupportCase).toHaveBeenCalledWith({
      caseId: 'SUP-1',
      category: 'chat_issue',
      editToken: 'tok',
      severity: 'high',
      summary: 'New summary',
      uploadUrl: 'https://s.example/api/v1/upload',
      userDescription: 'new details'
    })
    expect(ticket()).toMatchObject({
      category: 'chat_issue',
      description: 'new details',
      severity: 'high',
      status: 'IN_PROGRESS',
      summary: 'New summary'
    })
  })

  it('removes a withdrawn ticket and marks one closed meanwhile as resolved', async () => {
    window.hermesDesktop = {
      withdrawSupportCase: vi.fn().mockResolvedValue({ code: 409, error: 'case is already closed', ok: false })
    } as unknown as typeof window.hermesDesktop

    expect((await withdrawSupportTicket(ticket())).ok).toBe(false)
    expect(ticket()).toMatchObject({ editToken: undefined, status: 'RESOLVED' })

    window.hermesDesktop = {
      withdrawSupportCase: vi.fn().mockResolvedValue({ code: 200, ok: true, status: 'RESOLVED' })
    } as unknown as typeof window.hermesDesktop

    expect((await withdrawSupportTicket(ticket())).ok).toBe(true)
    expect(ticket()).toBeUndefined()
  })
})
