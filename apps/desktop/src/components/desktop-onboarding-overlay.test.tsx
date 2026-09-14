import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'
import type { OAuthProvider } from '@/types/hermes'

import { Picker } from './desktop-onboarding-overlay'

function provider(id: string, name = id): OAuthProvider {
  return {
    cli_command: `hermes login ${id}`,
    docs_url: `https://example.com/${id}`,
    flow: 'pkce',
    id,
    name,
    status: { logged_in: false }
  }
}

function setProviders(providers: OAuthProvider[]) {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false
  } satisfies DesktopOnboardingState)
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

afterEach(() => {
  cleanup()

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false
  })
})

describe('onboarding Picker', () => {
  it('features the common providers and hides the rest behind a disclosure', () => {
    setProviders([
      provider('nous', 'Nous Portal'),
      provider('google-gemini-cli', 'Google Gemini (OAuth)'),
      provider('anthropic', 'Anthropic Claude')
    ])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Anthropic (OAuth)')).toBeTruthy()
    expect(screen.getByText('Google Gemini (OAuth)')).toBeTruthy()
    expect(screen.getByText(/^Recommended$|^Empfohlen$/i)).toBeTruthy()
    expect(screen.queryByText('Nous Portal')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Other providers|Andere Anbieter/i }))

    expect(screen.getByText('Nous Portal')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Collapse|Einklappen/i })).toBeTruthy()
  })

  it('shows every provider directly when no common provider is present', () => {
    setProviders([provider('nous', 'Nous Portal'), provider('openai-codex', 'OpenAI Codex / ChatGPT')])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Nous Portal')).toBeTruthy()
    expect(screen.getByText('OpenAI OAuth (ChatGPT)')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Other providers|Andere Anbieter/i })).toBeNull()
    expect(screen.queryByText('Recommended')).toBeNull()
  })

  it('offers "choose later" on first run and persists the skip', () => {
    setProviders([provider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    const skip = screen.getByRole('button', { name: /I'll choose a provider later|Anbieter später wählen/i })

    fireEvent.click(skip)

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.localStorage.getItem('hermes-onboarding-skipped-v1')).toBe('1')
  })

  it('hides "choose later" in manual (add-provider) mode', () => {
    setProviders([provider('nous', 'Nous Portal')])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true })
    render(<Picker ctx={ctx} />)

    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})
