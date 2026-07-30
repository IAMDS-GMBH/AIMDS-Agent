import { atom } from 'nanostores'

import { notify } from '@/store/notifications'

export interface SavedSupportTicket {
  jobId: string
  caseId?: string
  referenceId?: string
  summary?: string
  category?: string
  severity?: string
  createdAt: number
  status?: string
  resolvedAt?: number
}

const STORAGE_KEY = 'hermes_support_tickets_history'
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

export function isTicketResolved(status?: string): boolean {
  if (!status) return false
  const s = status.toUpperCase()
  return s === 'RESOLVED' || s === 'COMPLETED' || s === 'ARCHIVED'
}

function loadSavedTickets(): SavedSupportTicket[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []

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
  if (!ticket.jobId && !ticket.referenceId && !ticket.caseId) return

  const item: SavedSupportTicket = {
    jobId: ticket.jobId || ticket.referenceId || ticket.caseId || `ticket-${Date.now()}`,
    caseId: ticket.caseId || ticket.referenceId || ticket.jobId,
    referenceId: ticket.referenceId || ticket.caseId || ticket.jobId,
    summary: ticket.summary || 'Support Report',
    category: ticket.category || 'other',
    severity: ticket.severity || 'medium',
    createdAt: ticket.createdAt || Date.now(),
    status: ticket.status || 'OPEN',
    resolvedAt: ticket.resolvedAt
  }

  const current = $supportTickets.get()
  const filtered = current.filter(t => t.jobId !== item.jobId && t.caseId !== item.caseId)
  const updated = [item, ...filtered].slice(0, 30)
  persistTickets(updated)
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
              window.dispatchEvent(new CustomEvent('hermes:open-gateway-settings', { detail: { tab: 'support' } }))
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
