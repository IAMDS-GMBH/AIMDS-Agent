import { describe, expect, it } from 'vitest'

import type { UsageStats } from '@/types/hermes'

import { de } from '../i18n/de'
import { en } from '../i18n/en'

import { formatModelPlainLabel } from './model-status-label'
import {
  connectionState,
  contextPercent,
  contextTone,
  COWORKER_STATUSBAR_ITEM_IDS,
  statusbarItemsForPosture,
  TECHNICAL_STATUSBAR_ITEM_IDS
} from './statusbar'

const items = [
  'connection',
  'command-center',
  'gateway-health',
  'agents',
  'cron',
  'running-timer',
  'context-usage',
  'context-ring',
  'session-timer',
  'model-summary',
  'support-tickets-status',
  'version-client'
].map(id => ({ id }))

const ids = (list: { id: string }[]) => list.map(item => item.id)

// AIS-794: the desktop status bar is the co-worker posture by default; the
// technical items need Settings → "Technische Details" (developer view).
describe('status bar posture (AIS-794)', () => {
  it('shows the co-worker items by default: connection dot, model, context ring', () => {
    expect(ids(statusbarItemsForPosture(items, false))).toEqual([
      'connection',
      'context-ring',
      'model-summary',
      'support-tickets-status',
      'version-client'
    ])
  })

  it('shows gateway, agents, cron, token numbers and timers only with technical details', () => {
    const technical = ids(statusbarItemsForPosture(items, true))

    for (const id of TECHNICAL_STATUSBAR_ITEM_IDS) {
      expect(technical).toContain(id)
    }

    for (const id of COWORKER_STATUSBAR_ITEM_IDS) {
      expect(technical).not.toContain(id)
    }
  })

  it('passes items a view adds through in both postures', () => {
    const extra = [{ id: 'skills-refresh' }]

    expect(statusbarItemsForPosture(extra, false)).toEqual(extra)
    expect(statusbarItemsForPosture(extra, true)).toEqual(extra)
  })

  it('derives the connection dot from the gateway and the model check', () => {
    expect(connectionState('open', { ready: true })).toBe('ready')
    expect(connectionState('open', { ready: false })).toBe('setup')
    expect(connectionState('open', null)).toBe('connecting')
    expect(connectionState('connecting', null)).toBe('connecting')
    expect(connectionState('closed', { ready: true })).toBe('offline')
  })

  it('fills the context ring from the usage, hidden when the limit is unknown', () => {
    expect(contextPercent({ context_max: 200_000, context_percent: 34.4, total: 0 } as UsageStats)).toBe(34)
    expect(contextPercent({ context_max: 200_000, context_percent: 140, total: 0 } as UsageStats)).toBe(100)
    expect(contextPercent({ total: 1200 } as UsageStats)).toBeNull()
    expect([contextTone(10), contextTone(75), contextTone(95)]).toEqual(['calm', 'filling', 'full'])
  })

  it('names just the model, without environment, effort or tool count', () => {
    expect(formatModelPlainLabel('AIMDS-Suite-Auto')).toBe('AIMDS Suite Auto')
    expect(formatModelPlainLabel('anthropic/claude-opus-4.8-fast')).toBe('Opus 4.8')
    expect(formatModelPlainLabel('  ')).toBe('')
  })

  it('keeps tech terms out of the co-worker texts in both languages', () => {
    const TECH = /\b(gateway|token|tokens|context|kontext|api|rpc|mcp|cron|backend)\b/i

    for (const copy of [en.shell.statusbar, de.shell.statusbar]) {
      for (const text of [...Object.values(copy.connection), copy.connectionSetupHint, copy.contextRing(42)]) {
        expect(text).not.toMatch(TECH)
      }
    }
  })
})
