import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Regression/feature tests for the AtlassianMCP/TempoMCP multi-instance
// install dialog: a "create new instance" vs "edit existing instance"
// picker, surfaced only for catalog entries flagged multi_instance.

// Radix Select calls scrollIntoView on its items when the content opens;
// jsdom doesn't implement it (nor hasPointerCapture/releasePointerCapture),
// so stub them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getEnvVars = vi.fn()
const getMcpCatalog = vi.fn()
const getHermesConfigRecord = vi.fn()
const installMcpCatalogEntry = vi.fn()
const removeMcpServer = vi.fn()

vi.mock('@/hermes', () => ({
  disconnectOAuthProvider: vi.fn(),
  getEnvVars: () => getEnvVars(),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  revealEnvVar: vi.fn(),
  getMcpCatalog: () => getMcpCatalog(),
  installMcpCatalogEntry: (body: unknown) => installMcpCatalogEntry(body),
  keycloakLogin: vi.fn(),
  listOAuthProviders: vi.fn(),
  removeMcpServer: (name: string) => removeMcpServer(name),
  saveHermesConfig: vi.fn(),
  setEnvVar: vi.fn()
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: { get: () => ({}) },
  startManualProviderOAuth: vi.fn()
}))

function atlassianEntry(overrides: Record<string, unknown> = {}) {
  return {
    name: 'AtlassianMCP',
    description: 'Jira integration',
    transport: 'stdio',
    multi_instance: true,
    instances: [],
    auth: {
      type: 'api_key',
      env: [
        { name: 'JIRA_URL', prompt: 'Jira URL', secret: false, required: true },
        { name: 'JIRA_PERSONAL_TOKEN', prompt: 'Token', secret: true, required: true }
      ]
    },
    required_env: [
      { name: 'JIRA_URL', prompt: 'Jira URL', secret: false, required: true },
      { name: 'JIRA_PERSONAL_TOKEN', prompt: 'Token', secret: true, required: true }
    ],
    ...overrides
  }
}

beforeEach(() => {
  getEnvVars.mockResolvedValue({})
  installMcpCatalogEntry.mockResolvedValue({ ok: true, name: 'AtlassianMCP' })
  removeMcpServer.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderCatalogSettings() {
  const { McpCatalogSettings } = await import('./providers-settings')

  return render(<McpCatalogSettings />)
}

describe('McpCatalogSection multi-instance install dialog', () => {
  it('does not show the instance picker for a non-multi-instance entry', async () => {
    getMcpCatalog.mockResolvedValue({
      entries: [
        {
          name: 'GithubMCP',
          description: 'GitHub integration',
          transport: 'http',
          auth: { type: 'oauth' },
          required_env: []
        }
      ]
    })
    getHermesConfigRecord.mockResolvedValue({ mcp_servers: {} })

    await renderCatalogSettings()

    fireEvent.click(await screen.findByRole('button', { name: /Installieren|Install/ }))
    await screen.findByText(/GithubMCP konfigurieren|Configure GithubMCP/)

    expect(screen.queryByText('Instanz')).toBeNull()
    expect(screen.queryByText('Instance')).toBeNull()
  })

  it('shows the instance picker defaulting to "create new" for AtlassianMCP', async () => {
    getMcpCatalog.mockResolvedValue({ entries: [atlassianEntry()] })
    getHermesConfigRecord.mockResolvedValue({ mcp_servers: {} })

    await renderCatalogSettings()

    fireEvent.click(await screen.findByRole('button', { name: /Installieren|Install/ }))
    await screen.findByText(/AtlassianMCP konfigurieren|Configure AtlassianMCP/)

    expect(await screen.findByText(/\+ Neue Instanz anlegen|\+ Create new instance/)).toBeTruthy()
  })

  it('lists existing instances and installs a second one under a custom instance_name', async () => {
    getMcpCatalog.mockResolvedValue({
      entries: [atlassianEntry({ instances: ['AtlassianMCP'] })]
    })
    getHermesConfigRecord.mockResolvedValue({
      mcp_servers: {
        AtlassianMCP: {
          command: 'uvx',
          env: { JIRA_URL: '${JIRA_URL}', JIRA_PERSONAL_TOKEN: '${JIRA_PERSONAL_TOKEN}' }
        }
      }
    })

    await renderCatalogSettings()

    fireEvent.click(await screen.findByRole('button', { name: /Bearbeiten|Server bearbeiten|Installieren|Install|Edit/ }))
    await screen.findByText(/AtlassianMCP konfigurieren|Configure AtlassianMCP/)

    // Existing instance shows up as a picker option.
    const trigger = await screen.findByRole('combobox')
    fireEvent.click(trigger)
    expect(await screen.findAllByRole('option')).toHaveLength(2) // "create new" + AtlassianMCP
    // Close the popover without changing the selection (Escape) rather than
    // clicking an option -- Radix marks background content aria-hidden while
    // the popover portal is open, which would hide the dialog from
    // subsequent role/text queries until it's closed again.
    fireEvent.keyDown(trigger, { key: 'Escape' })

    // Default mode is "create new"; type a custom instance name, then fill
    // the required fields and submit.
    const nameInput = await screen.findByPlaceholderText('AtlassianMCP')
    fireEvent.change(nameInput, { target: { value: 'EVNAtlassianMCP' } })

    const urlInput = await screen.findByPlaceholderText('JIRA_URL')
    fireEvent.change(urlInput, { target: { value: 'https://jira.apps.evn.at' } })
    const tokenInput = await screen.findByPlaceholderText('JIRA_PERSONAL_TOKEN')
    fireEvent.change(tokenInput, { target: { value: 'evn-secret-token' } })

    fireEvent.click(await screen.findByRole('button', { name: /Installieren|Install/ }))

    await waitFor(() =>
      expect(installMcpCatalogEntry).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'AtlassianMCP',
          instance_name: 'EVNAtlassianMCP',
          env: expect.objectContaining({
            JIRA_URL: 'https://jira.apps.evn.at',
            JIRA_PERSONAL_TOKEN: 'evn-secret-token'
          })
        })
      )
    )
  })

  it('selecting an existing instance loads its literal env values for editing', async () => {
    getMcpCatalog.mockResolvedValue({
      entries: [atlassianEntry({ instances: ['AtlassianMCP', 'EVNAtlassianMCP'] })]
    })
    getHermesConfigRecord.mockResolvedValue({
      mcp_servers: {
        AtlassianMCP: {
          command: 'uvx',
          env: { JIRA_URL: '${JIRA_URL}', JIRA_PERSONAL_TOKEN: '${JIRA_PERSONAL_TOKEN}' }
        },
        EVNAtlassianMCP: {
          command: 'uvx',
          env: { JIRA_URL: 'https://jira.apps.evn.at', JIRA_PERSONAL_TOKEN: 'evn-secret-token' }
        }
      }
    })

    await renderCatalogSettings()

    fireEvent.click(await screen.findByRole('button', { name: /Bearbeiten|Server bearbeiten|Installieren|Install|Edit/ }))
    await screen.findByText(/AtlassianMCP konfigurieren|Configure AtlassianMCP/)

    const trigger = await screen.findByRole('combobox')
    fireEvent.click(trigger)
    const option = await screen.findByText('EVNAtlassianMCP')
    fireEvent.click(option)

    const urlInput = (await screen.findByPlaceholderText('JIRA_URL')) as HTMLInputElement
    await waitFor(() => expect(urlInput.value).toBe('https://jira.apps.evn.at'))
  })
})
