import { describe, expect, it } from 'vitest'

import { clarifyArgsQuestions, readClarifyOutcome, readClarifyQuestions } from './clarify'
import { FOUR_OPEN_POINTS } from './clarify.fixtures'

describe('readClarifyQuestions', () => {
  it('reads the contract items and drops empty ones', () => {
    expect(
      readClarifyQuestions([
        { question: ' Load test? ', choices: ['Before', '', 'After'], topic: ' QS-38 ' },
        { question: '' },
        'Plain question?'
      ])
    ).toEqual([
      { question: 'Load test?', choices: ['Before', 'After'], topic: 'QS-38' },
      { question: 'Plain question?' }
    ])
  })

  it('accepts a JSON string and ignores anything else', () => {
    expect(readClarifyQuestions(JSON.stringify(FOUR_OPEN_POINTS))).toHaveLength(4)
    expect(readClarifyQuestions({ question: 'x' })).toEqual([])
    expect(readClarifyQuestions(undefined)).toEqual([])
  })
})

describe('clarifyArgsQuestions', () => {
  it('prefers questions, falls back to the single form', () => {
    expect(clarifyArgsQuestions({ questions: FOUR_OPEN_POINTS })).toHaveLength(4)
    expect(clarifyArgsQuestions({ question: 'Region?', choices: ['A'] })).toEqual([
      { question: 'Region?', choices: ['A'] }
    ])
    expect(clarifyArgsQuestions({})).toEqual([])
  })
})

describe('readClarifyOutcome', () => {
  it('pairs every question with its answer', () => {
    const rows = readClarifyOutcome(
      { questions: FOUR_OPEN_POINTS },
      {
        v: 1,
        questions: FOUR_OPEN_POINTS,
        answers: [
          { state: 'answered', response: 'Before the release' },
          { state: 'skipped', response: '' }
        ]
      }
    )

    expect(rows?.map(row => [row.topic, row.state, row.response])).toEqual([
      ['QS-38', 'answered', 'Before the release'],
      ['QS-41', 'skipped', ''],
      ['Release 0.7.15', 'empty', ''],
      ['Docs', 'empty', '']
    ])
  })

  it('reads the single form, and plain answers of older sessions', () => {
    expect(
      readClarifyOutcome(
        { question: 'Region?' },
        JSON.stringify({ question: 'Region?', user_response: 'Bavaria', response_state: 'answered' })
      )
    ).toEqual([{ question: 'Region?', response: 'Bavaria', state: 'answered' }])
    expect(readClarifyOutcome({ question: 'Region?' }, 'Bavaria')).toEqual([
      { question: 'Region?', response: 'Bavaria', state: 'answered' }
    ])
  })

  it('maps unknown states and leaves errors to the plain tool row', () => {
    expect(
      readClarifyOutcome({ question: 'Q?' }, { question: 'Q?', user_response: '', response_state: 'odd' })?.[0]?.state
    ).toBe('empty')
    expect(readClarifyOutcome({ question: 'Q?' }, { error: 'Clarify tool is not available' })).toBeNull()
    expect(readClarifyOutcome({}, {})).toBeNull()
  })
})
