import { atom } from 'nanostores'

import type { DesktopSupportCaseResult } from '@/global'
import { notify } from '@/store/notifications'

export interface SavedSupportTicket {
  jobId: string
  caseId?: string
  referenceId?: string
  summary?: string
  /** What the reporter wrote, kept locally so the edit form can prefill it. */
  description?: string
  category?: string
  severity?: string
  createdAt: number
  status?: string
  resolvedAt?: number
  /** AIS-399: lets the reporter edit/withdraw the case; reports from older
   * clients or servers have none and stay read-only. */
  editToken?: string
  uploadUrl?: string
}

const STORAGE_KEY = 'hermes_support_tickets_history'
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

export function isTicketResolved(status?: string, caseStatus?: string): boolean {
  const check = (s?: string) => {
    if (!s) {return false}
    const u = s.toUpperCase()

    return u === 'RESOLVED' || u === 'COMPLETED' || u === 'ARCHIVED' || u === 'REVIEW'
  }

  return check(status) || check(caseStatus)
}

function loadSavedTickets(): SavedSupportTicket[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)

    if (!raw) {return []}
    const parsed = JSON.parse(raw)

    if (!Array.isArray(parsed)) {return []}

    const now = Date.now()

    return parsed.filter((ticket: SavedSupportTicket) => {
      if (isTicketResolved(ticket.status)) {
        const resolvedTime = ticket.resolvedAt || ticket.createdAt

        if (now - resolvedTime > SEVEN_DAYS_MS) {
          return false
        }
      }

      return true
    })
  } catch {
    return []
  }
}

export const $supportTickets = atom<SavedSupportTicket[]>(loadSavedTickets())

function persistTickets(tickets: SavedSupportTicket[]) {
  $supportTickets.set(tickets)

  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tickets))
  } catch {
    // best-effort
  }
}

export function addSupportTicket(ticket: Omit<SavedSupportTicket, 'createdAt'> & { createdAt?: number }) {
  if (!ticket.jobId && !ticket.referenceId && !ticket.caseId) {return}

  const item: SavedSupportTicket = {
    jobId: ticket.jobId || ticket.referenceId || ticket.caseId || `ticket-${Date.now()}`,
    caseId: ticket.caseId || ticket.referenceId || ticket.jobId,
    referenceId: ticket.referenceId || ticket.caseId || ticket.jobId,
    summary: ticket.summary || 'Support Report',
    category: ticket.category || 'other',
    severity: ticket.severity || 'medium',
    createdAt: ticket.createdAt || Date.now(),
    status: ticket.status || 'OPEN',
    resolvedAt: ticket.resolvedAt,
    description: ticket.description,
    editToken: ticket.editToken || undefined,
    uploadUrl: ticket.uploadUrl
  }

  const current = $supportTickets.get()
  const filtered = current.filter(t => t.jobId !== item.jobId && t.caseId !== item.caseId)
  const updated = [item, ...filtered].slice(0, 30)
  persistTickets(updated)
}

/** Whether the reporter can still edit or withdraw this ticket. */
export function canEditSupportTicket(ticket: SavedSupportTicket): boolean {
  return Boolean(ticket.editToken && ticket.caseId) && !isTicketResolved(ticket.status)
}

function patchSupportTicket(jobId: string, patch: Partial<SavedSupportTicket>) {
  persistTickets($supportTickets.get().map(t => (t.jobId === jobId ? { ...t, ...patch } : t)))
}

export interface SupportTicketEdit {
  summary: string
  description: string
  category: string
  severity: string
}

function caseRequest(ticket: SavedSupportTicket) {
  return { caseId: ticket.caseId || '', editToken: ticket.editToken || '', uploadUrl: ticket.uploadUrl }
}

// A 409 "already closed" means the support team resolved it meanwhile.
function syncClosedTicket(ticket: SavedSupportTicket, result: DesktopSupportCaseResult) {
  if (result.code === 409 && /closed/i.test(result.error || '')) {
    patchSupportTicket(ticket.jobId, { editToken: undefined, resolvedAt: Date.now(), status: 'RESOLVED' })
  }
}

export async function editSupportTicket(
  ticket: SavedSupportTicket,
  edit: SupportTicketEdit
): Promise<DesktopSupportCaseResult> {
  const fn = window.hermesDesktop?.editSupportCase

  if (!fn) {
    return { code: 0, error: 'unavailable', ok: false }
  }

  const result = await fn({
    ...caseRequest(ticket),
    category: edit.category,
    severity: edit.severity,
    summary: edit.summary,
    userDescription: edit.description
  })

  if (result.ok) {
    patchSupportTicket(ticket.jobId, {
      category: edit.category,
      description: edit.description,
      severity: edit.severity,
      status: result.status || ticket.status,
      summary: edit.summary
    })
  } else {
    syncClosedTicket(ticket, result)
  }

  return result
}

export async function withdrawSupportTicket(ticket: SavedSupportTicket): Promise<DesktopSupportCaseResult> {
  const fn = window.hermesDesktop?.withdrawSupportCase

  if (!fn) {
    return { code: 0, error: 'unavailable', ok: false }
  }

  const result = await fn(caseRequest(ticket))

  if (result.ok) {
    removeSupportTicket(ticket.jobId)
  } else {
    syncClosedTicket(ticket, result)
  }

  return result
}

export function removeSupportTicket(jobId: string) {
  const current = $supportTickets.get()
  const filtered = current.filter(t => t.jobId !== jobId && t.caseId !== jobId && t.referenceId !== jobId)
  persistTickets(filtered)
}

export function updateAndCleanupSupportTickets(
  statusMap: Record<string, { case_status?: string; status?: string }>
) {
  const current = $supportTickets.get()
  const now = Date.now()
  const updated: SavedSupportTicket[] = []

  for (const ticket of current) {
    const live = statusMap[ticket.jobId] || statusMap[ticket.referenceId || ''] || statusMap[ticket.caseId || '']
    const liveStatus = live?.case_status || live?.status || ticket.status
    const oldStatus = ticket.status

    if (liveStatus && oldStatus && liveStatus.toUpperCase() !== oldStatus.toUpperCase()) {
      notify({
        id: `ticket-status-change-${ticket.jobId}-${liveStatus}`,
        kind: isTicketResolved(liveStatus) ? 'success' : 'info',
        title: 'Support-Ticket Status-Update',
        message: `Status für „${ticket.summary || ticket.referenceId}“ hat sich von ${oldStatus} auf ${liveStatus} geändert.`,
        durationMs: 10000,
        action: {
          label: 'Tickets anzeigen',
          onClick: () => {
            if (typeof window !== 'undefined') {
              window.dispatchEvent(
                new CustomEvent('hermes:open-command-center', { detail: { section: 'support' } })
              )
            }
          }
        }
      })
    }

    const resolved = isTicketResolved(liveStatus)

    let resolvedAt = ticket.resolvedAt

    if (resolved && !resolvedAt) {
      resolvedAt = now
    }

    if (resolved) {
      const resolvedTime = resolvedAt || ticket.createdAt

      if (now - resolvedTime > SEVEN_DAYS_MS) {
        continue
      }
    }

    updated.push({
      ...ticket,
      status: liveStatus,
      resolvedAt: resolved ? resolvedAt : undefined
    })
  }

  persistTickets(updated)
}

export function clearResolvedSupportTickets(
  statusMap?: Record<string, { case_status?: string; status?: string }>
) {
  const current = $supportTickets.get()

  const filtered = current.filter(ticket => {
    const live = statusMap ? (statusMap[ticket.jobId] || statusMap[ticket.referenceId || ''] || statusMap[ticket.caseId || '']) : undefined
    const status = live?.case_status || live?.status || ticket.status

    return !isTicketResolved(status)
  })

  persistTickets(filtered)
}

export function clearSupportTickets() {
  persistTickets([])
}

export async function checkSupportTicketsStatus(): Promise<Record<string, { case_status?: string; status?: string }>> {
  const current = $supportTickets.get()

  if (current.length === 0) {
    return {}
  }

  const statusMap: Record<string, { case_status?: string; status?: string }> = {}

  await Promise.all(
    current.map(async ticket => {
      const candidates = Array.from(new Set([ticket.jobId, ticket.caseId, ticket.referenceId].filter(Boolean) as string[]))

      for (const targetId of candidates) {
        try {
          const url = `https://suite-support.iamds.com/api/v1/jobs/${encodeURIComponent(targetId)}`
          const resp = await fetch(url, { method: 'GET', headers: { 'User-Agent': 'hermes-desktop-ticket-check/1.0' } })

          if (resp.ok) {
            const data = await resp.json()

            if (data && (data.case_status || data.status)) {
              const entry = {
                case_status: data.case_status,
                status: data.status
              }

              if (ticket.jobId) {statusMap[ticket.jobId] = entry}

              if (ticket.caseId) {statusMap[ticket.caseId] = entry}

              if (ticket.referenceId) {statusMap[ticket.referenceId] = entry}

              break
            }
          }
        } catch {
          // best-effort
        }
      }
    })
  )

  if (Object.keys(statusMap).length > 0) {
    updateAndCleanupSupportTickets(statusMap)
  }

  return statusMap
}
