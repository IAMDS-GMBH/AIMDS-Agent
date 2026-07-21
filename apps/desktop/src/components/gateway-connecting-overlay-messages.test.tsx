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

    // Should render a business loading message like "Kompiliere Kaffee-Zufuhr..."
    await waitFor(() => {
      expect(screen.getByText('Kompiliere Kaffee-Zufuhr...')).toBeTruthy()
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

    // Should render an English business loading message like "Compiling coffee supply..."
    await waitFor(() => {
      expect(screen.getByText('Compiling coffee supply...')).toBeTruthy()
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

    // Should render a team/developer message like "Martin beendet gerade das EVN-Meeting."
    await waitFor(() => {
      expect(screen.getByText('Martin beendet gerade das EVN-Meeting.')).toBeTruthy()
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

    // Should render an English team/developer message like "Martin is wrapping up the EVN meeting."
    await waitFor(() => {
      expect(screen.getByText('Martin is wrapping up the EVN meeting.')).toBeTruthy()
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
      expect(screen.getByText('Martin beendet gerade das EVN-Meeting.')).toBeTruthy()
    })
  })
})
