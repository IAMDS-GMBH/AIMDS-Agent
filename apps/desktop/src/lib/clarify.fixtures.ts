import type { ClarifyQuestion } from './clarify'

// AIS-793 (session 20261030_150532_431390): the model packed four open points
// into one "Wie soll es weitergehen?" with four mixed choices. As intended:
// four questions, each with its topic. Used by the tests and the screenshots.
export const FOUR_OPEN_POINTS: ClarifyQuestion[] = [
  {
    choices: ['Before the release', 'After the release'],
    question: 'Should the load test run before or after the release?',
    topic: 'QS-38'
  },
  {
    choices: ['Johannes', 'Tobias', 'The product team'],
    question: 'Who signs off the acceptance test?',
    topic: 'QS-41'
  },
  { choices: ['Yes', 'No, next week'], question: 'Ship the desktop build with this release?', topic: 'Release 0.7.15' },
  { question: 'Where should the release notes go?', topic: 'Docs' }
]
