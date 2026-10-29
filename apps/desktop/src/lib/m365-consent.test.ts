import { describe, expect, it } from 'vitest'

import { de } from '@/i18n/de'
import { en } from '@/i18n/en'

import { m365ConsentPill, m365ConsentView } from './m365-consent'

// AIS-755: the consent pill follows the granted tier, not `org_consented`.
describe('m365 consent view', () => {
  it('is full only for the widest tier', () => {
    expect(m365ConsentView({ granted_tier: 'channels', org_consented: true })).toEqual({ missing: [], state: 'full' })
    expect(m365ConsentView({ granted_tier: 'admin', org_consented: true })).toEqual({ missing: ['channels'], state: 'partial' })
    expect(m365ConsentView({ granted_tier: 'standard', org_consented: true })).toEqual({
      missing: ['groups', 'directory', 'channels'],
      state: 'partial'
    })
    expect(m365ConsentView({ granted_tier: 'self', org_consented: false })).toEqual({
      missing: ['org', 'groups', 'directory', 'channels'],
      state: 'self'
    })
    expect(m365ConsentView({ granted_tier: null, org_consented: false }).state).toBe('self')
  })

  it('treats an unknown, newer tier as fully approved', () => {
    expect(m365ConsentView({ granted_tier: 'everything' as never, org_consented: true }).state).toBe('full')
  })

  it('names what is missing in plain words, in both languages', () => {
    const view = m365ConsentView({ granted_tier: 'admin', org_consented: true })

    expect(m365ConsentPill(view, en.settings.providers.m365)).toEqual({
      hint: 'Your organization still has to approve reading and posting in Teams channels.',
      label: 'Partly approved',
      tone: 'muted'
    })
    expect(m365ConsentPill(view, de.settings.providers.m365)).toEqual({
      hint: 'Deine Organisation muss noch freigeben: Lesen und Posten in Teams-Kanälen.',
      label: 'Teilweise freigegeben',
      tone: 'muted'
    })
    expect(m365ConsentPill(m365ConsentView({ granted_tier: 'groups', org_consented: true }), en.settings.providers.m365).hint).toBe(
      'Your organization still has to approve the company directory and SharePoint sites and reading and posting in Teams channels.'
    )
    expect(m365ConsentPill(m365ConsentView({ granted_tier: 'channels', org_consented: true }), de.settings.providers.m365)).toEqual({
      hint: null,
      label: 'Organisation freigegeben',
      tone: 'primary'
    })
  })
})
