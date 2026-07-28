import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Regression test for a bug where OAuthAccountsPanel hardcoded a
// GitHub-only allowlist, hiding every other configured OAuth provider
// (Microsoft, Nous, etc.) from the dashboard's "Connected Accounts" list.
// The fix removes the allowlist and instead respects the backend's own
// per-provider `hidden` flag (used e.g. to keep iamds-keycloak out of this
// list since it's surfaced elsewhere).

const listOAuthProviders = vi.fn()
const disconnectOAuthProvider = vi.fn()

vi.mock('@/hermes', () => ({
  listOAuthProviders: () => listOAuthProviders(),
  disconnectOAuthProvider: (id: string) => disconnectOAuthProvider(id)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

import { OAuthAccountsPanel } from './providers-settings'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function makeProvider(overrides: Record<string, unknown> = {}) {
  return {
    cli_command: 'hermes auth add example',
    docs_url: 'https://example.com',
    flow: 'device_code',
    id: 'example',
    name: 'Example',
    status: { logged_in: false },
    ...overrides
  }
}

describe('OAuthAccountsPanel', () => {
  it('shows all non-hidden providers, not just GitHub', async () => {
    listOAuthProviders.mockResolvedValue({
      providers: [
        makeProvider({ id: 'github', name: 'GitHub (OAuth)', status: { logged_in: true } }),
        makeProvider({ id: 'microsoft', name: 'Microsoft 365 (OAuth)' }),
        makeProvider({ id: 'iamds-keycloak', name: 'IAMDS LiteLLM (Keycloak SSO)', hidden: true })
      ]
    })

    render(<OAuthAccountsPanel />)

    await waitFor(() => {
      expect(screen.queryByText('GitHub (OAuth)')).toBeTruthy()
    })

    expect(screen.getByText('Microsoft 365 (OAuth)')).toBeTruthy()
    expect(screen.queryByText('IAMDS LiteLLM (Keycloak SSO)')).toBeNull()
  })
})
