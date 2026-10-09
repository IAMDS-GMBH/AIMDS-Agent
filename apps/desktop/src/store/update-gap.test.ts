import { describe, expect, it } from 'vitest'

import { currentVersionOf, isOutdated, releaseGap, targetVersionOf } from './update-gap'

describe('releaseGap', () => {
  it('counts patch releases within a minor', () => {
    expect(releaseGap('0.7.6-rc.11', 'v0.7.10')).toBe(4)
    expect(releaseGap('v0.7.9', '0.7.10')).toBe(1)
  })

  it('treats a new minor or major as far behind', () => {
    expect(releaseGap('0.7.10', '0.8.0')).toBe(99)
    expect(releaseGap('0.7.10', '1.0.0')).toBe(99)
  })

  it('is 0 when not behind or unknown', () => {
    expect(releaseGap('0.7.10', '0.7.10')).toBe(0)
    expect(releaseGap('0.7.11', '0.7.10')).toBe(0)
    expect(releaseGap('', '0.7.10')).toBe(0)
    expect(releaseGap('main', 'v0.7.10')).toBe(0)
  })
})

describe('isOutdated', () => {
  const week = 7 * 24 * 60 * 60 * 1000

  it('two releases or a week of waiting', () => {
    expect(isOutdated('v0.7.8-rc.1', 'v0.7.10', null)).toBe(true)
    expect(isOutdated('v0.7.9', 'v0.7.10', null)).toBe(false)
    expect(isOutdated('v0.7.9', 'v0.7.10', 0 + 1, week + 2)).toBe(true)
    expect(isOutdated('v0.7.9', 'v0.7.10', 1, week - 10)).toBe(false)
  })
})

describe('version labels', () => {
  it('prefers the release marker and adds the v', () => {
    expect(currentVersionOf({ releaseVersion: '0.7.6-rc.11', supported: true })).toBe('v0.7.6-rc.11')
    expect(currentVersionOf({ headTag: 'v0.7.9', supported: true })).toBe('v0.7.9')
    expect(targetVersionOf({ supported: true, targetVersion: '0.7.10' })).toBe('v0.7.10')
    expect(currentVersionOf(null)).toBe('')
  })
})
