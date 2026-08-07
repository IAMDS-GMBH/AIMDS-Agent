import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $desktopBoot } from '@/store/boot'
import { $gatewayState, setGatewayState } from '@/store/session'
import { GatewayConnectingOverlay } from './gateway-connecting-overlay'

let mockLocale = 'de'
let mockConfig: any = { model: { base_url: '' } }
let mockEnvVars: any = {}

vi.mock('@/i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      ...actual.useI18n(),
      locale: mockLocale
    })
  }
})

vi.mock('@/hermes', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hermes')>()
  return {
    ...actual,
    getHermesConfig: async () => mockConfig,
    getEnvVars: async () => mockEnvVars
  }
})

function resetStores() {
  setGatewayState('idle')
  mockLocale = 'de'
  mockConfig = { model: { base_url: '' } }
  mockEnvVars = {}
  try { localStorage.clear() } catch {}
  $desktopBoot.set({
    error: null,
    fakeMode: false,
    message: 'ready',
    phase: 'renderer.ready',
    progress: 100,
    running: false,
    timestamp: Date.now(),
    visible: false
  })
}

beforeEach(() => {
  resetStores()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  // Assign directly to existing window to avoid clobbering JSDOM globals like setInterval
  ;(window as any).hermesDesktop = {
    getConnectionConfig: async () => ({
      mode: 'local',
      remoteUrl: ''
    })
  }
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  delete (window as any).hermesDesktop
})

describe('GatewayConnectingOverlay Messages Gating', () => {
  it('renders German business loading messages by default (not on iamds.com, locale de)', async () => {
    setGatewayState('idle')
    $desktopBoot.set({
      ...$desktopBoot.get(),
      running: true,
      phase: 'renderer.init',
      visible: true
    })

    render(<GatewayConnectingOverlay />)

    // Should render a business loading message
    await waitFor(() => {
      expect(screen.getByText('Analysiere Arbeitsschritte und optimiere Workflows...')).toBeTruthy()
    })
  })

  it('renders English business loading messages (not on iamds.com, locale en)', async () => {
    mockLocale = 'en'
    setGatewayState('idle')
    $desktopBoot.set({
      ...$desktopBoot.get(),
      running: true,
      phase: 'renderer.init',
      visible: true
    })

    render(<GatewayConnectingOverlay />)

    // Should render an English business loading message
    await waitFor(() => {
      expect(screen.getByText('Analyzing steps and optimizing workflows...')).toBeTruthy()
    })
  })

  it('renders German team/easter messages when iamds.com is configured (locale de)', async () => {
    mockConfig = { model: { base_url: 'https://staging.suite.iamds.com/litellm/v1' } }
    setGatewayState('idle')
    $desktopBoot.set({
      ...$desktopBoot.get(),
      running: true,
      phase: 'renderer.init',
      visible: true
    })

    render(<GatewayConnectingOverlay />)

    // Should render a team/developer message like "Patrick aktiviert Arbeitskräfte..."
    await waitFor(() => {
      expect(screen.getByText('Patrick aktiviert Arbeitskräfte...')).toBeTruthy()
    })
  })

  it('renders English team/easter messages when iamds.com is configured (locale en)', async () => {
    mockLocale = 'en'
    mockConfig = { model: { base_url: 'https://staging.suite.iamds.com/litellm/v1' } }
    setGatewayState('idle')
    $desktopBoot.set({
      ...$desktopBoot.get(),
      running: true,
      phase: 'renderer.init',
      visible: true
    })

    render(<GatewayConnectingOverlay />)

    // Should render an English team/developer message like "Patrick is activating manpower..."
    await waitFor(() => {
      expect(screen.getByText('Patrick is activating manpower...')).toBeTruthy()
    })
  })

  it('detects iamds.com via remoteUrl in connection config', async () => {
    ;(window as any).hermesDesktop = {
      getConnectionConfig: async () => ({
        mode: 'remote',
        remoteUrl: 'https://dev.suite.iamds.com'
      })
    }

    setGatewayState('idle')
    $desktopBoot.set({
      ...$desktopBoot.get(),
      running: true,
      phase: 'renderer.init',
      visible: true
    })

    render(<GatewayConnectingOverlay />)

    // Since remoteUrl has iamds.com, it should render team messages
    await waitFor(() => {
      expect(screen.getByText('Patrick aktiviert Arbeitskräfte...')).toBeTruthy()
    })
  })

  it('detects iamds.com via staging provider URL', async () => {
    mockConfig = {
      model: { base_url: '' },
      providers: {
        'iamds-litellm-staging': { base_url: 'https://staging.suite.iamds.com' }
      }
    }

    render(<GatewayConnectingOverlay />)
    
    // Should detect IAMDS from staging URL
    await waitFor(() => {
      expect(screen.getByText(/Martin|Tobias|Michael|Johannes/)).toBeTruthy()
    })
  })

  it('detects iamds.com via dev provider URL', async () => {
    mockConfig = {
      model: { base_url: '' },
      providers: {
        'iamds-litellm-dev': { base_url: 'https://dev.suite.iamds.com:5000/v1' }
      }
    }

    render(<GatewayConnectingOverlay />)
    
    // Should detect IAMDS from dev URL even with port and path
    await waitFor(() => {
      expect(screen.getByText(/Martin|Tobias|Michael|Johannes/)).toBeTruthy()
    })
  })
})
