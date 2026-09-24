import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesModule from '@/hermes'
import type * as I18nModule from '@/i18n'
import { $desktopBoot } from '@/store/boot'
import { setGatewayState } from '@/store/session'

import {
  composedTeamMessage,
  GatewayConnectingOverlay,
  TEAM_ACTIVITIES_DE,
  TEAM_ACTIVITIES_EN,
  TEAM_FIXED_MESSAGES_DE,
  TEAM_FIXED_MESSAGES_EN,
  TEAM_NAMES,
  teamMessage
} from './gateway-connecting-overlay'

let mockLocale = 'de'
let mockConfig: any = { model: { base_url: '' } }
let mockEnvVars: any = {}

vi.mock('@/i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof I18nModule>()

  return {
    ...actual,
    useI18n: () => ({
      ...actual.useI18n(),
      locale: mockLocale
    })
  }
})

vi.mock('@/hermes', async (importOriginal) => {
  const actual = await importOriginal<typeof HermesModule>()

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

  try { localStorage.clear() } catch { /* storage unavailable in jsdom variants */ }
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

describe('teamMessage composition', () => {
  it('walks every name/activity pairing before repeating any of them', () => {
    // 9 names and 42 activities share the factor 3, so indexing both with a
    // plain `step % length` would pin each activity to a subset of names and
    // shrink the pool to a fraction. This is the regression that guards it.
    const period = TEAM_NAMES.length * TEAM_ACTIVITIES_DE.length
    const seen = new Set<string>()

    for (let step = 0; step < period; step++) {
      seen.add(composedTeamMessage(step, TEAM_ACTIVITIES_DE))
    }

    expect(seen.size).toBe(period)
  })

  it('pairs each name with every activity equally often over one period', () => {
    const perName = new Map<string, number>()

    for (let step = 0; step < TEAM_NAMES.length * TEAM_ACTIVITIES_EN.length; step++) {
      const name = composedTeamMessage(step, TEAM_ACTIVITIES_EN).split(' ')[0]

      perName.set(name, (perName.get(name) ?? 0) + 1)
    }

    expect(perName.size).toBe(TEAM_NAMES.length)
    expect([...perName.values()].every(count => count === TEAM_ACTIVITIES_EN.length)).toBe(true)
  })

  it('keeps the name-bound and composed lists parallel across locales', () => {
    expect(TEAM_ACTIVITIES_EN.length).toBe(TEAM_ACTIVITIES_DE.length)
    expect(TEAM_FIXED_MESSAGES_EN.length).toBe(TEAM_FIXED_MESSAGES_DE.length)
  })

  it('reserves every eighth slot for the name-bound messages and rotates them', () => {
    const drawnOnFixedSlots = [7, 15, 23, 31].map(step =>
      teamMessage(step, TEAM_ACTIVITIES_DE, TEAM_FIXED_MESSAGES_DE)
    )

    expect(drawnOnFixedSlots).toEqual([
      TEAM_FIXED_MESSAGES_DE[0],
      TEAM_FIXED_MESSAGES_DE[1],
      TEAM_FIXED_MESSAGES_DE[2],
      TEAM_FIXED_MESSAGES_DE[0]
    ])
  })

  it('starts on a composed message and loses no composed entry to the fixed slots', () => {
    // Step 0 must stay composed — the overlay tests below assert the very first
    // message verbatim. The composed walk must also skip nothing: over a stretch
    // covering more than one activity wrap, every composed message is distinct.
    expect(teamMessage(0, TEAM_ACTIVITIES_DE, TEAM_FIXED_MESSAGES_DE)).toBe(
      composedTeamMessage(0, TEAM_ACTIVITIES_DE)
    )

    const composed = []

    for (let step = 0; step < 96; step++) {
      const message = teamMessage(step, TEAM_ACTIVITIES_DE, TEAM_FIXED_MESSAGES_DE)

      if (!TEAM_FIXED_MESSAGES_DE.includes(message as never)) {
        composed.push(message)
      }
    }

    expect(new Set(composed).size).toBe(composed.length)
  })
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
      expect(screen.getByText('Patrick aktiviert Arbeitskräfte...')).toBeTruthy()
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
      expect(screen.getByText('Patrick aktiviert Arbeitskräfte...')).toBeTruthy()
    })
  })
})
