import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ReconnectingNotice, shouldShowReconnectingNotice } from './reconnecting-notice'

const wrapper = (locale: 'de' | 'en') =>
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nProvider configClient={null} initialLocale={locale}>
        {children}
      </I18nProvider>
    )
  }

afterEach(cleanup)

describe('ReconnectingNotice (AIS-735)', () => {
  it('shows only while a running chat has lost its connection', () => {
    expect(shouldShowReconnectingNotice('closed', true, true)).toBe(true)
    expect(shouldShowReconnectingNotice('connecting', true, true)).toBe(true)
    expect(shouldShowReconnectingNotice('open', true, true)).toBe(false)
    expect(shouldShowReconnectingNotice('closed', false, true)).toBe(false)
    expect(shouldShowReconnectingNotice('closed', true, false)).toBe(false)
  })

  it('says the agent keeps working, in the user language', () => {
    render(<ReconnectingNotice visible />, { wrapper: wrapper('de') })
    expect(screen.getByTestId('reconnecting-notice').textContent).toContain('ich arbeite weiter')
    cleanup()

    render(<ReconnectingNotice visible />, { wrapper: wrapper('en') })
    expect(screen.getByTestId('reconnecting-notice').textContent).toContain('still working')
  })

  it('renders nothing when hidden', () => {
    render(<ReconnectingNotice visible={false} />, { wrapper: wrapper('en') })
    expect(screen.queryByTestId('reconnecting-notice')).toBeNull()
  })
})
