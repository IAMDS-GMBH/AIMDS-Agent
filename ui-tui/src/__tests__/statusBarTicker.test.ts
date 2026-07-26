import { describe, expect, it } from 'vitest'

import { formatSlidingVerb, padVerb, SLIDE_IN_STEPS, SLIDE_HOLD_STEPS, TOTAL_SLIDE_STEPS, VERB_PAD_LEN } from '../components/appChrome.js'
import { VERBS } from '../content/verbs.js'

describe('FaceTicker verb padding and sliding animation', () => {
  it('pads every verb to the same width', () => {
    for (const verb of VERBS) {
      expect(padVerb(verb)).toHaveLength(VERB_PAD_LEN)
    }
  })

  it('keeps trailing ellipsis attached', () => {
    for (const verb of VERBS) {
      expect(padVerb(verb).startsWith(`${verb}…`)).toBe(true)
    }
  })

  it('preserves exact VERB_PAD_LEN width across all sliding animation steps', () => {
    const verb = 'analyzing'
    for (let step = 0; step < TOTAL_SLIDE_STEPS * 2; step++) {
      const formatted = formatSlidingVerb(verb, step)
      expect(formatted).toHaveLength(VERB_PAD_LEN)
    }
  })

  it('holds padded verb still during hold phase', () => {
    const verb = 'reasoning'
    const expected = padVerb(verb)
    const holdStart = SLIDE_IN_STEPS
    const holdEnd = SLIDE_IN_STEPS + SLIDE_HOLD_STEPS - 1

    expect(formatSlidingVerb(verb, holdStart)).toBe(expected)
    expect(formatSlidingVerb(verb, holdEnd)).toBe(expected)
  })
})
