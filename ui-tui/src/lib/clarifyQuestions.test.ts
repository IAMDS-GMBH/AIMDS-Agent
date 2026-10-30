import { describe, expect, it } from 'vitest'

import { advanceClarify, clarifyRequestFromPayload, formatClarifyAnswers } from './clarifyQuestions.js'

// AIS-793: four open points as four questions with topics.
const FOUR_OPEN_POINTS = [
  {
    choices: ['Before the release', 'After the release'],
    question: 'Should the load test run before or after the release?',
    topic: 'QS-38'
  },
  { choices: ['Johannes', 'Tobias', 'The product team'], question: 'Who signs off the acceptance test?', topic: 'QS-41' },
  { choices: ['Yes', 'No, next week'], question: 'Ship the desktop build with this release?', topic: 'Release 0.7.15' },
  { question: 'Where should the release notes go?', topic: 'Docs' }
]

describe('clarifyRequestFromPayload', () => {
  it('keeps the single form as it was', () => {
    expect(clarifyRequestFromPayload({ choices: ['A'], question: 'Pick?', request_id: 'r1' })).toEqual({
      choices: ['A'],
      question: 'Pick?',
      requestId: 'r1'
    })
  })

  it('starts a several-question ask at the first question, numbered with its topic', () => {
    const req = clarifyRequestFromPayload({
      choices: FOUR_OPEN_POINTS[0]!.choices,
      question: FOUR_OPEN_POINTS[0]!.question,
      questions: FOUR_OPEN_POINTS,
      request_id: 'r2'
    })

    expect(req.question).toBe('(1/4) QS-38: Should the load test run before or after the release?')
    expect(req.choices).toEqual(['Before the release', 'After the release'])
    expect(req.index).toBe(0)
    expect(req.questions).toHaveLength(4)
  })
})

describe('advanceClarify', () => {
  it('steps through the questions and answers them together', () => {
    let req = clarifyRequestFromPayload({ choices: null, question: '', questions: FOUR_OPEN_POINTS, request_id: 'r3' })
    const answers = ['After the release', 'Tobias', 'Yes', 'In the wiki']
    const seen: string[] = []
    let result = advanceClarify(req, answers[0]!)

    for (let i = 1; i < answers.length; i += 1) {
      expect(result.next).not.toBeNull()
      req = result.next!
      seen.push(req.question)
      result = advanceClarify(req, answers[i]!)
    }

    expect(seen).toEqual([
      '(2/4) QS-41: Who signs off the acceptance test?',
      '(3/4) Release 0.7.15: Ship the desktop build with this release?',
      '(4/4) Docs: Where should the release notes go?'
    ])
    expect(result.next).toBeNull()
    expect(result.answers).toEqual(answers.map(response => ({ response, state: 'answered' })))
  })

  it('ends the ask on a cancel and leaves the rest empty', () => {
    const req = clarifyRequestFromPayload({ choices: null, question: '', questions: FOUR_OPEN_POINTS, request_id: 'r4' })
    const first = advanceClarify(req, 'Before the release')
    const cancelled = advanceClarify(first.next!, '')

    expect(cancelled.next).toBeNull()
    expect(cancelled.answers.map(a => a.state)).toEqual(['answered', 'empty', 'empty', 'empty'])
  })

  it('answers a single question at once', () => {
    const req = clarifyRequestFromPayload({ choices: null, question: 'Region?', request_id: 'r5' })

    expect(advanceClarify(req, 'Bavaria')).toEqual({ answers: [{ response: 'Bavaria', state: 'answered' }], next: null })
  })
})

describe('formatClarifyAnswers', () => {
  it('lists every question with its answer', () => {
    const text = formatClarifyAnswers(FOUR_OPEN_POINTS.slice(0, 2), [
      { response: 'Before the release', state: 'answered' },
      { response: '', state: 'empty' }
    ])

    expect(text).toBe(
      'QS-38: Should the load test run before or after the release? → Before the release\n' +
        'QS-41: Who signs off the acceptance test? → (no answer)'
    )
  })
})
