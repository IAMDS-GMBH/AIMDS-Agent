import { beforeEach, describe, expect, it } from 'vitest'

import { $activeGatewayProfile } from '@/store/profile'

import {
  $threadWidth,
  applyThreadWidth,
  assignThreadWidth,
  DEFAULT_THREAD_WIDTH,
  installThreadWidth,
  normalizeThreadWidth,
  resolveThreadWidth,
  setThreadWidth
} from './thread-width'

describe('chat width setting', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $activeGatewayProfile.set('default')
    $threadWidth.set(DEFAULT_THREAD_WIDTH)
    delete document.documentElement.dataset.threadWidth
  })

  it('defaults to wide and normalizes unknown values', () => {
    expect(DEFAULT_THREAD_WIDTH).toBe('wide')
    expect(resolveThreadWidth('default')).toBe('wide')
    expect(normalizeThreadWidth('huge')).toBe('wide')
    expect(normalizeThreadWidth('normal')).toBe('normal')
  })

  it('is stored per profile, unassigned profiles inherit the default profile', () => {
    assignThreadWidth('default', 'full')
    assignThreadWidth('work', 'normal')

    expect(resolveThreadWidth('default')).toBe('full')
    expect(resolveThreadWidth('work')).toBe('normal')
    expect(resolveThreadWidth('never-set')).toBe('full')
  })

  it('maps the value onto <html> (wide needs no attribute)', () => {
    const root = document.documentElement

    applyThreadWidth('normal', root)
    expect(root.dataset.threadWidth).toBe('normal')

    applyThreadWidth('wide', root)
    expect(root.dataset.threadWidth).toBeUndefined()

    applyThreadWidth('full', root)
    expect(root.dataset.threadWidth).toBe('full')
  })

  it('saves a pick for the active profile and follows profile switches', () => {
    installThreadWidth()

    $activeGatewayProfile.set('work')
    setThreadWidth('normal')
    expect(window.localStorage.getItem('hermes-desktop-profile-thread-widths-v1')).toContain('"work":"normal"')
    expect(document.documentElement.dataset.threadWidth).toBe('normal')

    $activeGatewayProfile.set('default')
    expect($threadWidth.get()).toBe('wide')
    expect(document.documentElement.dataset.threadWidth).toBeUndefined()

    $activeGatewayProfile.set('work')
    expect($threadWidth.get()).toBe('normal')
  })
})
