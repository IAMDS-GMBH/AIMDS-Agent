import { afterEach, describe, expect, it, vi } from 'vitest'

import { $notifications } from '@/store/notifications'

import { checkOpenProjectSuiteNotice } from './use-openproject-suite-notice'

function stubDesktop(notice: unknown) {
  const desktop = {
    api: vi.fn(async () => ({ notice })),
    notify: vi.fn(async () => true)
  }

  window.hermesDesktop = desktop as unknown as typeof window.hermesDesktop

  return desktop
}

describe('OpenProject Suite notice (AIS-483)', () => {
  afterEach(() => {
    window.hermesDesktop = undefined as unknown as typeof window.hermesDesktop
    window.localStorage.clear()
  })

  it('tells the user once per notice, in-app and as an OS notification', async () => {
    const desktop = stubDesktop({ id: 'n1', instance: 'https://op.example.com', kind: 'local_replaced', login: 'jh' })

    expect(await checkOpenProjectSuiteNotice()).toBe(true)
    expect(desktop.notify).toHaveBeenCalledTimes(1)
    expect(JSON.stringify(desktop.notify.mock.calls[0])).toContain('jh')
    expect(JSON.stringify($notifications.get())).toContain('op.example.com')

    expect(await checkOpenProjectSuiteNotice()).toBe(false)
    expect(desktop.notify).toHaveBeenCalledTimes(1)
  })

  it('stays quiet without a notice or when the backend is unreachable', async () => {
    stubDesktop(null)
    expect(await checkOpenProjectSuiteNotice()).toBe(false)

    window.hermesDesktop = { api: vi.fn(async () => Promise.reject(new Error('down'))) } as unknown as typeof window.hermesDesktop
    expect(await checkOpenProjectSuiteNotice()).toBe(false)
  })
})
