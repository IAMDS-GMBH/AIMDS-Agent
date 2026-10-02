import { describe, expect, it } from 'vitest'

import { parseTurnPhase } from '@/store/turn-phase'

import { rotatingPick, turnPhaseLine } from './turn-phase-copy'

const business = { elapsed: 5, locale: 'de', nerdy: false }

describe('turn phase copy (AIS-460)', () => {
  it('says plainly that the agent reads a long history, and explains a long wait', () => {
    const phase = parseTurnPhase({ attempt: 1, max_attempts: 3, messages: 79, phase: 'waiting' })

    expect(turnPhaseLine(phase ?? undefined, business)).toEqual({
      line: 'Ich lese den bisherigen Verlauf (79 Nachrichten) …'
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
      'I am summarizing the conversation so far so we can keep going …'
    )
    expect(turnPhaseLine(parseTurnPhase({ phase: 'switching_model' }) ?? undefined, en).line).toBe(
      'I am switching to another AI model …'
    )
  })

  it('falls back to a plain sentence without any phase event', () => {
    expect(turnPhaseLine(undefined, business)).toEqual({ line: 'Ich denke nach …' })
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

        expect(`${line} ${hint ?? ''}`).not.toMatch(/token|cache|fallback|http|api|hermes/i)
      }
    }
  })

  it('rotates the playful lines while the wait goes on', () => {
    const phase = parseTurnPhase({ messages: 79, phase: 'waiting' }) ?? undefined
    const lines = new Set([0, 12, 24, 36].map(elapsed => turnPhaseLine(phase, { elapsed, locale: 'de', nerdy: true, seed: 0 }).line))

    expect(lines.size).toBeGreaterThan(1)
  })

  it('changes playful lines at random gaps, never on a fixed beat and never twice in a row', () => {
    const items = ['a', 'b', 'c', 'd', 'e', 'f']
    const changes: number[] = []
    let previous = rotatingPick(items, 1234, 0)

    for (let t = 1; t <= 600; t += 1) {
      const current = rotatingPick(items, 1234, t)

      if (current !== previous) {
        changes.push(t)
      }

      previous = current
    }

    const gaps = changes.slice(1).map((t, i) => t - changes[i])

    expect(changes.length).toBeGreaterThan(10)
    expect(new Set(gaps).size).toBeGreaterThan(3)
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(10)
    expect(Math.max(...gaps)).toBeLessThanOrEqual(31)
  })

  it('starts each phase with its own pick', () => {
    const items = Array.from({ length: 12 }, (_, i) => `line ${i}`)
    const firstPicks = new Set(Array.from({ length: 20 }, (_, seed) => rotatingPick(items, seed * 7919, 0)))

    expect(firstPicks.size).toBeGreaterThan(4)
  })

  it('ignores unknown phases', () => {
    expect(parseTurnPhase({ phase: 'hacking' })).toBeNull()
    expect(parseTurnPhase(null)).toBeNull()
  })
})
