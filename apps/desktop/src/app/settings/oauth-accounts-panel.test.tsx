import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

// AIS-325: the Accounts page shows every connected account, then the short
// list of common providers that can still be set up (Anthropic, Gemini,
// OpenRouter, Groq, custom endpoint) plus the Microsoft 365 account (an
// account, not a model provider — it carries the tenant consent control).
// Other catalog entries (Nous, Qwen, …) are not advertised here, and the
// backend's `hidden` flag is respected.

const listOAuthProviders = vi.fn()
const disconnectOAuthProvider = vi.fn()
const startManualProviderOAuth = vi.fn()
const startManualApiKeyEntry = vi.fn()

vi.mock('@/hermes', () => ({
  listOAuthProviders: () => listOAuthProviders(),
  disconnectOAuthProvider: (id: string) => disconnectOAuthProvider(id)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: { subscribe: () => () => undefined },
  startManualProviderOAuth: (id: string) => startManualProviderOAuth(id),
  startManualApiKeyEntry: (envKey: string) => startManualApiKeyEntry(envKey)
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

function renderPanel(vars: Record<string, { is_set: boolean }> = {}) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <OAuthAccountsPanel vars={vars as never} />
    </I18nProvider>
  )
}

const CATALOG = [
  makeProvider({ id: 'github', name: 'GitHub (OAuth)', status: { logged_in: true, source_label: 'GitHub Account Token (…JFmW)' } }),
  makeProvider({ id: 'microsoft', name: 'Microsoft 365 (OAuth)' }),
  makeProvider({ id: 'nous', name: 'Nous Portal' }),
  makeProvider({ id: 'qwen-oauth', name: 'Qwen (via Qwen CLI)', flow: 'external' }),
  makeProvider({ id: 'google-gemini-cli', name: 'Google Gemini (OAuth)', flow: 'loopback' }),
  makeProvider({ id: 'anthropic', name: 'Anthropic (OAuth)', flow: 'pkce' }),
  makeProvider({ id: 'claude-code', name: 'Claude Code (OAuth)', flow: 'external', status: { logged_in: true, source_label: '~/.claude/.credentials.json' } }),
  makeProvider({ id: 'iamds-keycloak', name: 'IAMDS LiteLLM (Keycloak SSO)', hidden: true })
]

describe('OAuthAccountsPanel', () => {
  it('splits connected accounts from the common providers still available', async () => {
    listOAuthProviders.mockResolvedValue({ providers: CATALOG })

    renderPanel({ OPENROUTER_API_KEY: { is_set: true } })

    await waitFor(() => {
      expect(screen.queryByText('GitHub (OAuth)')).toBeTruthy()
    })

    const connected = within(screen.getByTestId('connected-accounts'))
    expect(connected.getByText('GitHub (OAuth)')).toBeTruthy()
    expect(connected.getByText('Claude Code (OAuth)')).toBeTruthy()
    expect(connected.getByText('~/.claude/.credentials.json')).toBeTruthy()
    expect(connected.queryByText('Anthropic (OAuth)')).toBeNull()

    const available = within(screen.getByTestId('available-providers'))
    const names = available.getAllByText(/OAuth\)|OpenRouter|Groq|Custom endpoint/).map(el => el.textContent)
    expect(names).toEqual(['Anthropic (OAuth)', 'Google Gemini (OAuth)', 'OpenRouter', 'Groq', 'Custom endpoint', 'Microsoft 365 (OAuth)'])

    // Not advertised: other catalog entries and hidden ones.
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.queryByText('Qwen (via Qwen CLI)')).toBeNull()
    expect(screen.queryByText('IAMDS LiteLLM (Keycloak SSO)')).toBeNull()

    // A pasted key shows as configured, the rest as not connected.
    const openrouter = within(screen.getByTestId('account-row-openrouter'))
    expect(openrouter.getByText('Configured')).toBeTruthy()
    const groq = within(screen.getByTestId('account-row-groq'))
    expect(groq.getByText('Not connected')).toBeTruthy()
  })

  it('routes connect clicks to the OAuth or API-key hand-off', async () => {
    listOAuthProviders.mockResolvedValue({ providers: CATALOG })

    renderPanel()

    await waitFor(() => {
      expect(screen.queryByTestId('available-providers')).toBeTruthy()
    })

    fireEvent.click(within(screen.getByTestId('account-row-gemini')).getByRole('button', { name: 'Connect' }))
    expect(startManualProviderOAuth).toHaveBeenCalledWith('google-gemini-cli')

    fireEvent.click(within(screen.getByTestId('account-row-groq')).getByRole('button', { name: 'Connect' }))
    expect(startManualApiKeyEntry).toHaveBeenCalledWith('GROQ_API_KEY')

    fireEvent.click(within(screen.getByTestId('account-row-custom')).getByRole('button', { name: 'Connect' }))
    expect(startManualApiKeyEntry).toHaveBeenCalledWith('OPENAI_BASE_URL')
  })

  it('moves a provider out of the available list once it is connected', async () => {
    listOAuthProviders.mockResolvedValue({
      providers: [
        makeProvider({ id: 'google-gemini-cli', name: 'Google Gemini (OAuth)', flow: 'loopback', status: { logged_in: true, source_label: 'user@example.com' } }),
        makeProvider({ id: 'anthropic', name: 'Anthropic (OAuth)', flow: 'pkce' })
      ]
    })

    renderPanel()

    await waitFor(() => {
      expect(screen.queryByTestId('connected-accounts')).toBeTruthy()
    })

    const connected = within(screen.getByTestId('connected-accounts'))
    expect(connected.getByText('Google Gemini (OAuth)')).toBeTruthy()
    expect(connected.getByText('user@example.com')).toBeTruthy()
    expect(connected.getByRole('button', { name: 'Disconnect' })).toBeTruthy()

    const available = within(screen.getByTestId('available-providers'))
    expect(available.queryByText('Google Gemini (OAuth)')).toBeNull()
    expect(available.getByText('Anthropic (OAuth)')).toBeTruthy()
  })
})
