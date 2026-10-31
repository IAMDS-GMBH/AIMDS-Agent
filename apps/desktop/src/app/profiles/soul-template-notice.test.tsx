import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { SoulTemplateNotice } from './soul-template-notice'

const acknowledgeProfileSoulTemplate = vi.fn()

vi.mock('@/hermes', () => ({
  acknowledgeProfileSoulTemplate: (name: string) => acknowledgeProfileSoulTemplate(name)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nProvider configClient={null} initialLocale="de">
    {children}
  </I18nProvider>
)

const TEMPLATE = {
  status: 'edited' as const,
  notice: true,
  template_hash: 'abc',
  template: '# New template\n',
  diff: '--- SOUL.md\n+++ template\n-# Mine\n+# New template\n'
}

afterEach(() => {
  cleanup()
  acknowledgeProfileSoulTemplate.mockReset()
})

describe('SoulTemplateNotice (AIS-795)', () => {
  it('says a new template exists without touching the owner text', () => {
    render(<SoulTemplateNotice onDismissed={vi.fn()} onLoad={vi.fn()} profileName="default" template={TEMPLATE} />, {
      wrapper
    })

    expect(screen.getByText('Neue Vorlage verfügbar')).toBeTruthy()
    expect(screen.queryByText('+# New template')).toBeNull()
  })

  it('shows the differences on request', () => {
    render(<SoulTemplateNotice onDismissed={vi.fn()} onLoad={vi.fn()} profileName="default" template={TEMPLATE} />, {
      wrapper
    })

    fireEvent.click(screen.getByText('Unterschiede anzeigen'))
    expect(screen.getByText('+# New template')).toBeTruthy()
    expect(screen.getByText('-# Mine')).toBeTruthy()
  })

  it('loads the template into the editor and leaves saving to the owner', () => {
    const onLoad = vi.fn()
    render(<SoulTemplateNotice onDismissed={vi.fn()} onLoad={onLoad} profileName="default" template={TEMPLATE} />, {
      wrapper
    })

    fireEvent.click(screen.getByText('Vorlage in den Editor laden'))
    expect(onLoad).toHaveBeenCalledWith('# New template\n')
    expect(acknowledgeProfileSoulTemplate).not.toHaveBeenCalled()
  })

  it('dismisses the notice for this template version', () => {
    acknowledgeProfileSoulTemplate.mockResolvedValue({ ok: true, template_hash: 'abc' })
    const onDismissed = vi.fn()
    render(<SoulTemplateNotice onDismissed={onDismissed} onLoad={vi.fn()} profileName="work" template={TEMPLATE} />, {
      wrapper
    })

    fireEvent.click(screen.getByText('Ausblenden'))
    expect(onDismissed).toHaveBeenCalled()
    expect(acknowledgeProfileSoulTemplate).toHaveBeenCalledWith('work')
  })
})
