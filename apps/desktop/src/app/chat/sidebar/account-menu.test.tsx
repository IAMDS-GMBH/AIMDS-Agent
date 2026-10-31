import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $activeGatewayProfile, $profiles, $showAllProfiles } from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

import { AccountMenu } from './profile-switcher'

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)

const profile = (name: string, isDefault = false) => ({ is_default: isDefault, name }) as ProfileInfo

function LocationProbe() {
  const location = useLocation()

  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderMenu() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <AccountMenu />
        <LocationProbe />
      </MemoryRouter>
    </I18nProvider>
  )

  const trigger = screen.getByTestId('account-menu')

  fireEvent.keyDown(trigger, { key: 'Enter' })

  return trigger
}

// AIS-794: the home / + / … profile rail became one account menu.
describe('account menu (AIS-794)', () => {
  beforeEach(() => {
    $profiles.set([profile('default', true), profile('work')])
    $activeGatewayProfile.set('default')
    $showAllProfiles.set(false)
  })

  afterEach(() => {
    cleanup()
    $profiles.set([])
  })

  it('shows the active profile and offers profiles, settings, accounts and help in one menu', () => {
    const trigger = renderMenu()

    expect(trigger.textContent).toContain('default')

    const items = screen.getAllByRole('menuitem').map(item => item.textContent?.trim())

    expect(items).toEqual([
      'Ddefault',
      'Wwork',
      'Alle Profile',
      'Neues Profil…',
      'Profile verwalten',
      'Einstellungen',
      'Konten',
      'Hilfe & Feedback'
    ])
  })

  it('opens the provider accounts page from "Konten"', () => {
    renderMenu()

    fireEvent.click(screen.getByRole('menuitem', { name: /Konten/ }))

    expect(screen.getByTestId('location').textContent).toBe('/settings?tab=providers&pview=accounts')
  })

  it('lists no "Alle Profile" entry for a single profile', () => {
    $profiles.set([profile('default', true)])
    renderMenu()

    expect(screen.queryByRole('menuitem', { name: /Alle Profile/ })).toBeNull()
  })
})
