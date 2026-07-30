import { atom } from 'nanostores'

export interface SavedSupportTicket {
  jobId: string
  caseId?: string
  referenceId?: string
  summary?: string
  category?: string
  severity?: string
  createdAt: number
}

const STORAGE_KEY = 'hermes_support_tickets_history'

function loadSavedTickets(): SavedSupportTicket[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export const $supportTickets = atom<SavedSupportTicket[]>(loadSavedTickets())

export function addSupportTicket(ticket: Omit<SavedSupportTicket, 'createdAt'> & { createdAt?: number }) {
  if (!ticket.jobId && !ticket.referenceId && !ticket.caseId) return

  const item: SavedSupportTicket = {
    jobId: ticket.jobId || ticket.referenceId || ticket.caseId || `ticket-${Date.now()}`,
    caseId: ticket.caseId || ticket.referenceId || ticket.jobId,
    referenceId: ticket.referenceId || ticket.caseId || ticket.jobId,
    summary: ticket.summary || 'Support Report',
    category: ticket.category || 'other',
    severity: ticket.severity || 'medium',
    createdAt: ticket.createdAt || Date.now()
  }

  const current = $supportTickets.get()
  const filtered = current.filter(t => t.jobId !== item.jobId && t.caseId !== item.caseId)
  const updated = [item, ...filtered].slice(0, 30)
  $supportTickets.set(updated)

  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  } catch {
    // best-effort
  }
}

export function clearSupportTickets() {
  $supportTickets.set([])
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // best-effort
  }
}
