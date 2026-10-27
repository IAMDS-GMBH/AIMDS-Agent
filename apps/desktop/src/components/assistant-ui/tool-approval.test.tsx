import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { $gateway } from '@/store/gateway'
import { $approvalRequest, clearAllPrompts, setApprovalRequest } from '@/store/prompts'
import { $activeSessionId } from '@/store/session'

import { PendingApprovalNotice } from './tool-approval'

function setRequest(command = 'rm -rf /tmp/x') {
  $activeSessionId.set('sess-1')
  setApprovalRequest({ command, description: 'dangerous command', sessionId: 'sess-1' })
}

function mockGateway() {
  const request = vi.fn().mockResolvedValue({ resolved: true })
  $gateway.set({ request } as unknown as HermesGateway)

  return request
}

afterEach(() => {
  cleanup()
  clearAllPrompts()
  $activeSessionId.set(null)
  $gateway.set(null)
})

describe('PendingApprovalNotice', () => {
  it('renders nothing when there is no pending approval', () => {
    const { container } = render(<PendingApprovalNotice />)

    expect(container.innerHTML).toBe('')
  })

  it('renders nothing for an approval of another session', () => {
    setRequest()
    $activeSessionId.set('sess-2')
    const { container } = render(<PendingApprovalNotice />)

    expect(container.innerHTML).toBe('')
  })

  it('says that it waits, in plain words, with the command behind a disclosure', () => {
    setRequest('rm -f /tmp/b13.txt')
    render(<PendingApprovalNotice />)

    expect(screen.getByRole('alert').textContent).toMatch(/Waiting for your approval|Wartet auf deine Freigabe/)
    expect(screen.getByText(/Show command|Befehl anzeigen/)).toBeTruthy()
    expect(screen.getByText('rm -f /tmp/b13.txt')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Run|Ausführen/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Reject|Ablehnen/ })).toBeTruthy()
  })

  it('sends approval.respond {choice: "once"} and clears the request on Run', async () => {
    const request = mockGateway()
    setRequest()
    render(<PendingApprovalNotice />)

    fireEvent.click(screen.getByRole('button', { name: /Run|Ausführen/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('approval.respond', { choice: 'once', session_id: 'sess-1' })
    })
    expect($approvalRequest.get()).toBeNull()
  })

  it('sends choice "deny" on Reject', async () => {
    const request = mockGateway()
    setRequest()
    render(<PendingApprovalNotice />)

    fireEvent.click(screen.getByRole('button', { name: /Reject|Ablehnen/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('approval.respond', { choice: 'deny', session_id: 'sess-1' })
    })
  })
})
