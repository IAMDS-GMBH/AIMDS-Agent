import { describe, expect, it } from 'vitest'

import { COMPOSER_UNAVAILABLE, setComposerText } from './composer-text'

function fakeAui(availableAfter: number) {
  let calls = 0
  const texts: string[] = []

  return {
    texts,
    composer() {
      return {
        setText(text: string) {
          calls += 1

          if (calls <= availableAfter) {
            throw new Error(COMPOSER_UNAVAILABLE)
          }

          texts.push(text)
        }
      }
    }
  }
}

function manualSchedule() {
  const queue: Array<() => void> = []

  return {
    schedule: (run: () => void) => void queue.push(run),
    flush() {
      while (queue.length) {
        queue.shift()!()
      }
    }
  }
}

describe('setComposerText', () => {
  it('writes immediately when the composer is mounted', () => {
    const aui = fakeAui(0)
    setComposerText(aui, 'hello', () => {
      throw new Error('must not schedule')
    })
    expect(aui.texts).toEqual(['hello'])
  })

  it('does not throw while the composer is not mounted and writes once it is', () => {
    const aui = fakeAui(2)
    const frames = manualSchedule()
    expect(() => setComposerText(aui, 'draft', frames.schedule)).not.toThrow()
    frames.flush()
    expect(aui.texts).toEqual(['draft'])
  })

  it('applies only the newest text after a pending retry', () => {
    const aui = fakeAui(1)
    const frames = manualSchedule()
    setComposerText(aui, 'first', frames.schedule)
    setComposerText(aui, 'second', frames.schedule)
    frames.flush()
    expect(aui.texts).toEqual(['second'])
  })

  it('gives up quietly when the composer never mounts', () => {
    const aui = fakeAui(Number.POSITIVE_INFINITY)
    const frames = manualSchedule()
    setComposerText(aui, 'lost', frames.schedule)
    expect(() => frames.flush()).not.toThrow()
    expect(aui.texts).toEqual([])
  })

  it('rethrows unrelated errors', () => {
    const aui = {
      composer: () => ({
        setText: () => {
          throw new Error('boom')
        }
      })
    }

    expect(() => setComposerText(aui, 'x')).toThrow('boom')
  })
})
