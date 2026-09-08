import { describe, expect, it } from 'vitest'

import { hasSummaryContent, parseCronOutputSummary, summaryFromApi } from './output-summary'

describe('parseCronOutputSummary', () => {
  it('reads plain marker lines', () => {
    const text = [
      '# Morning brief',
      '',
      'FINDING: Two PRs are waiting on review.',
      'NEXT: Review #62 first.',
      'OPEN_QUESTION: Ship rc.3 today?'
    ].join('\n')

    expect(parseCronOutputSummary(text)).toEqual({
      finding: 'Two PRs are waiting on review.',
      next: 'Review #62 first.',
      openQuestion: 'Ship rc.3 today?'
    })
  })

  it('tolerates bold labels, bullets, CRLF and trailing whitespace', () => {
    const text = [
      '- **FINDING:** Backlog grew by 4.   ',
      '* **NEXT**: Triage the new tickets.\r',
      '1. __OPEN QUESTION__: Who owns AIS-310?  ',
      ''
    ].join('\n')

    expect(parseCronOutputSummary(text)).toEqual({
      finding: 'Backlog grew by 4.',
      next: 'Triage the new tickets.',
      openQuestion: 'Who owns AIS-310?'
    })
  })

  it('keeps the first occurrence and strips a bold value wrapper', () => {
    const text = ['finding: **first**', 'FINDING: second'].join('\n')

    expect(parseCronOutputSummary(text).finding).toBe('first')
  })

  it('returns empty fields for text without markers', () => {
    expect(parseCronOutputSummary('just prose\nno markers here')).toEqual({ finding: '', next: '', openQuestion: '' })
    expect(parseCronOutputSummary(null)).toEqual({ finding: '', next: '', openQuestion: '' })
    expect(hasSummaryContent(parseCronOutputSummary(''))).toBe(false)
  })

  it('does not match markers in the middle of a sentence', () => {
    expect(parseCronOutputSummary('The FINDING: was wrong').finding).toBe('')
  })
})

describe('summaryFromApi', () => {
  it('maps the backend shape and trims', () => {
    expect(summaryFromApi({ finding: ' a ', next: 'b', open_question: undefined })).toEqual({
      finding: 'a',
      next: 'b',
      openQuestion: ''
    })
    expect(hasSummaryContent(summaryFromApi(null))).toBe(false)
  })
})
