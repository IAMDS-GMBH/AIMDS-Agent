import type { MicrosoftAdminConsentResponse } from '@/types/hermes'

/**
 * What the Microsoft 365 tenant consent pill on the Accounts page says
 * (AIS-755).
 *
 * The backend reports `granted_tier`: the widest scope tier the signed-in
 * account obtains silently (hermes_cli/m365_auth.py, M365_SCOPE_TIER_ORDER).
 * `org_consented` is true for every tier above "self", so a tenant that
 * approved before the channels tier existed (AIS-737) showed a green
 * "Organization approved" while posting to channels still failed. The pill now
 * follows the tier: green only for the widest tier, otherwise "partly
 * approved" plus what the organization still has to approve, in plain words.
 */

export type M365Tier = NonNullable<MicrosoftAdminConsentResponse['granted_tier']>

/** Narrowest → widest; mirrors M365_SCOPE_TIER_ORDER (reversed) in m365_auth.py. */
export const M365_TIER_ORDER: readonly M365Tier[] = ['self', 'standard', 'groups', 'admin', 'channels']

/** End-user capabilities each tier adds over the previous one. */
export type M365Capability = 'channels' | 'directory' | 'groups' | 'org'

const CAPABILITY_OF_TIER: Record<Exclude<M365Tier, 'self'>, M365Capability> = {
  admin: 'directory',
  channels: 'channels',
  groups: 'groups',
  standard: 'org'
}

export type M365ConsentState = 'full' | 'partial' | 'self'

export interface M365ConsentView {
  state: M365ConsentState
  /** What the organization still has to approve, narrowest first. */
  missing: M365Capability[]
}

export function m365ConsentView(info: Pick<MicrosoftAdminConsentResponse, 'granted_tier' | 'org_consented'>): M365ConsentView {
  const rank = info.granted_tier ? M365_TIER_ORDER.indexOf(info.granted_tier) : -1
  // An unknown future tier is wider than everything we know.
  const granted = rank < 0 && info.granted_tier ? M365_TIER_ORDER.length - 1 : Math.max(rank, info.org_consented ? 1 : 0)

  const missing = M365_TIER_ORDER.slice(granted + 1).map(tier => CAPABILITY_OF_TIER[tier as Exclude<M365Tier, 'self'>])

  if (!missing.length) {
    return { missing, state: 'full' }
  }

  return { missing, state: granted === 0 ? 'self' : 'partial' }
}

export interface M365ConsentCopy {
  orgApproved: string
  partlyApproved: string
  selfOnly: string
  capabilities: Record<M365Capability, string>
  stillNeedsApproval: (capabilities: string) => string
  listJoin: (items: string[]) => string
}

export interface M365ConsentPill {
  tone: 'muted' | 'primary'
  label: string
  /** End-user hint naming what is still missing; null when nothing is. */
  hint: null | string
}

export function m365ConsentPill(view: M365ConsentView, copy: M365ConsentCopy): M365ConsentPill {
  if (view.state === 'full') {
    return { hint: null, label: copy.orgApproved, tone: 'primary' }
  }

  return {
    hint: copy.stillNeedsApproval(copy.listJoin(view.missing.map(capability => copy.capabilities[capability]))),
    label: view.state === 'self' ? copy.selfOnly : copy.partlyApproved,
    tone: 'muted'
  }
}
