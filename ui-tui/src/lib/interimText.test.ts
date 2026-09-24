import { describe, expect, it } from 'vitest'

import { interimRemainder } from './interimText.js'

describe('interimRemainder', () => {
  it('drops interim text that is already buffered', () => {
    expect(interimRemainder('\n\nIch lege  das Ticket\nan.', 'Ich lege das Ticket an.')).toBe('')
  })

  it('keeps only the missing tail', () => {
    expect(interimRemainder('Ich lege das', 'Ich lege das Ticket an.')).toBe(' Ticket an.')
  })

  it('adds unrelated text as a new paragraph', () => {
    expect(interimRemainder('Erledigt.', 'Weiter.')).toBe('\n\nWeiter.')
    expect(interimRemainder('', ' Weiter. ')).toBe('Weiter.')
  })
})
