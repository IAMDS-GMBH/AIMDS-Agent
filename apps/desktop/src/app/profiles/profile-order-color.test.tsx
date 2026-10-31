import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { PROFILE_SWATCHES } from '@/lib/profile-color'
import {
  $profileColors,
  $profileOrder,
  moveInProfileOrder,
  moveProfile,
  setProfileColor,
  sortByProfileOrder
} from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

const profile = (name: string, isDefault = false) =>
  ({ has_env: false, is_default: isDefault, name, path: `/p/${name}`, skill_count: 0 }) as ProfileInfo

const PROFILES = [profile('default', true), profile('alpha'), profile('beta'), profile('gamma')]

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getProfiles: () => Promise.resolve({ profiles: PROFILES }),
  getProfileSoul: () => Promise.resolve({ content: '' })
}))

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)

const ORDER_KEY = 'hermes.desktop.profileOrder'
const COLORS_KEY = 'hermes.desktop.profileColors'

// AIS-794: the account menu replaced the rail, so colour and order moved to
// the profiles page and must write the same store the rail did.
describe('profile order and colour write path (AIS-794)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $profileOrder.set([])
    $profileColors.set({})
  })

  afterEach(() => {
    cleanup()
  })

  it('moves one step and leaves the ends alone', () => {
    expect(moveInProfileOrder(['a', 'b', 'c'], 'c', -1)).toEqual(['a', 'c', 'b'])
    expect(moveInProfileOrder(['a', 'b', 'c'], 'a', 1)).toEqual(['b', 'a', 'c'])
    expect(moveInProfileOrder(['a', 'b', 'c'], 'a', -1)).toEqual(['a', 'b', 'c'])
    expect(moveInProfileOrder(['a', 'b', 'c'], 'c', 1)).toEqual(['a', 'b', 'c'])
    expect(moveInProfileOrder(['a', 'b'], 'missing', 1)).toEqual(['a', 'b'])
  })

  it('stores the full order, which is the order the profile hotkeys follow', () => {
    moveProfile(['alpha', 'beta', 'gamma'], 'gamma', -1)

    expect($profileOrder.get()).toEqual(['alpha', 'gamma', 'beta'])
    expect(JSON.parse(window.localStorage.getItem(ORDER_KEY) ?? '[]')).toEqual(['alpha', 'gamma', 'beta'])

    // switchProfileToSlot(n) picks the n-th named profile in this order.
    const slots = sortByProfileOrder(
      PROFILES.filter(entry => !entry.is_default),
      $profileOrder.get()
    ).map(entry => entry.name)

    expect(slots).toEqual(['alpha', 'gamma', 'beta'])
  })

  it('stores a picked colour under the profile and clears it for automatic', () => {
    setProfileColor('alpha', PROFILE_SWATCHES[8])

    expect(JSON.parse(window.localStorage.getItem(COLORS_KEY) ?? '{}')).toEqual({ alpha: PROFILE_SWATCHES[8] })

    setProfileColor('alpha', null)

    expect($profileColors.get()).toEqual({})
  })

  it('reorders and recolours from the profiles page', async () => {
    const { ProfilesView } = await import('./index')

    render(
      <I18nProvider>
        <MemoryRouter>
          <ProfilesView onClose={() => undefined} />
        </MemoryRouter>
      </I18nProvider>
    )

    await waitFor(() => expect(screen.getByTestId('profile-row-gamma')).toBeTruthy())

    // The default profile leads and cannot move; the first named one cannot go up.
    expect(screen.queryByRole('button', { name: 'default nach oben verschieben' })).toBeNull()
    expect(screen.getByRole('button', { name: 'alpha nach oben verschieben' }).hasAttribute('disabled')).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'gamma nach oben verschieben' }))

    expect($profileOrder.get()).toEqual(['alpha', 'gamma', 'beta'])
    await waitFor(() =>
      expect(screen.getAllByTestId(/^profile-row-/).map(row => row.dataset.testid)).toEqual([
        'profile-row-default',
        'profile-row-alpha',
        'profile-row-gamma',
        'profile-row-beta'
      ])
    )

    fireEvent.click(screen.getByRole('button', { name: 'Farbe für beta' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'Blau für beta' }))

    expect($profileColors.get()).toEqual({ beta: PROFILE_SWATCHES[8] })

    fireEvent.click(screen.getByRole('button', { name: 'Farbe für beta' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'Automatische Farbe' }))

    expect($profileColors.get()).toEqual({})
  })
})
