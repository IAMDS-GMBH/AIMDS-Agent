import { describe, expect, it } from 'vitest'

import { parseTurnPhase } from '@/store/turn-phase'

import { turnPhaseLine } from './turn-phase-copy'

const business = { elapsed: 5, locale: 'de', nerdy: false }

describe('turn phase copy (AIS-460)', () => {
  it('says plainly that Hermes reads a long history, and explains a long wait', () => {
    const phase = parseTurnPhase({ attempt: 1, max_attempts: 3, messages: 79, phase: 'waiting' })

    expect(turnPhaseLine(phase ?? undefined, business)).toEqual({
      line: 'Hermes liest den bisherigen Verlauf (79 Nachrichten) …'
    })
    expect(turnPhaseLine(phase ?? undefined, { ...business, elapsed: 45 }).hint).toBe(
      'Bei einem langen Verlauf dauert die erste Antwort etwas länger.'
    )
  })

  it('names the retry and its reason without technical terms', () => {
    const retry = parseTurnPhase({ attempt: 2, max_attempts: 3, phase: 'retrying', reason: 'connection' })
    const next = parseTurnPhase({ attempt: 2, max_attempts: 3, messages: 79, phase: 'waiting' }, retry ?? undefined)

    expect(turnPhaseLine(retry ?? undefined, business).line).toContain('Verbindung zum KI-Dienst wurde unterbrochen')
    expect(turnPhaseLine(next ?? undefined, business).line).toBe(
      'Neuer Versuch (2 von 3) – die Verbindung zum KI-Dienst war kurz unterbrochen …'
    )
  })

  it('covers summarizing and switching models, in English too', () => {
    const en = { ...business, locale: 'en' }

    expect(turnPhaseLine(parseTurnPhase({ phase: 'compressing' }) ?? undefined, en).line).toBe(
      'Hermes is summarizing the conversation so far so it can continue …'
    )
    expect(turnPhaseLine(parseTurnPhase({ phase: 'switching_model' }) ?? undefined, en).line).toBe(
      'Hermes is switching to another AI model …'
    )
  })

  it('falls back to a plain sentence without any phase event', () => {
    expect(turnPhaseLine(undefined, business)).toEqual({ line: 'Hermes denkt nach …' })
  })

  it('never shows technical words in business mode', () => {
    const phases = [
      { messages: 79, phase: 'waiting' },
      { attempt: 2, max_attempts: 3, phase: 'retrying', reason: 'busy' },
      { phase: 'compressing' },
      { phase: 'switching_model' }
    ]

    for (const locale of ['de', 'en']) {
      for (const raw of phases) {
        const { hint, line } = turnPhaseLine(parseTurnPhase(raw) ?? undefined, { elapsed: 60, locale, nerdy: false })

        expect(`${line} ${hint ?? ''}`).not.toMatch(/token|cache|fallback|http|api/i)
      }
    }
  })

  it('rotates the playful lines while the wait goes on', () => {
    const phase = parseTurnPhase({ messages: 79, phase: 'waiting' }) ?? undefined
    const lines = new Set([0, 12, 24, 36].map(elapsed => turnPhaseLine(phase, { elapsed, locale: 'de', nerdy: true, seed: 0 }).line))

    expect(lines.size).toBeGreaterThan(1)
  })

  it('ignores unknown phases', () => {
    expect(parseTurnPhase({ phase: 'hacking' })).toBeNull()
    expect(parseTurnPhase(null)).toBeNull()
  })
})
