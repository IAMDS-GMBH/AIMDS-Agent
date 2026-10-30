import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $activeGatewayProfile } from './profile'
import {
  $developerView,
  $technicalDetails,
  $technicalDetailsSetting,
  developerViewPref,
  setDeveloperView,
  setTechnicalDetails
} from './view-mode'

// AIS-794: the desktop is the co-worker posture; the developer view is an
// opt-in per profile, stored like the skin and light/dark mode.
describe('developer view (AIS-794)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $activeGatewayProfile.set('default')
  })

  afterEach(() => {
    window.localStorage.clear()
    $activeGatewayProfile.set('default')
  })

  it('is off by default, and so are the technical details', () => {
    expect($developerView.get()).toBe(false)
    expect($technicalDetails.get()).toBe(false)
  })

  it('unlocks the technical details, which are on once the developer view is on', () => {
    setDeveloperView(true)

    expect($developerView.get()).toBe(true)
    expect($technicalDetails.get()).toBe(true)

    setTechnicalDetails(false)

    expect($technicalDetailsSetting.get()).toBe(false)
    expect($technicalDetails.get()).toBe(false)
  })

  it('never shows technical details without the developer view', () => {
    setTechnicalDetails(true)

    expect($technicalDetailsSetting.get()).toBe(true)
    expect($technicalDetails.get()).toBe(false)
  })

  it('is stored per profile; a named profile inherits the default until it has its own', () => {
    setDeveloperView(true)

    $activeGatewayProfile.set('work')
    expect($developerView.get()).toBe(true)

    setDeveloperView(false)
    expect($developerView.get()).toBe(false)

    $activeGatewayProfile.set('default')
    expect($developerView.get()).toBe(true)
    expect(developerViewPref.resolve('work')).toBe(false)
  })

  it('survives a reload (reads back from local storage)', () => {
    setDeveloperView(true)

    expect(window.localStorage.getItem('hermes-desktop-developer-view-v1')).toBe('true')
    expect(developerViewPref.resolve('default')).toBe(true)
  })
})
