const assert = require('node:assert/strict')
const test = require('node:test')

const {
  MACOS_TRAFFIC_LIGHTS_HEIGHT,
  NATIVE_OVERLAY_BUTTON_WIDTH,
  TITLEBAR_HEIGHT,
  WINDOW_BUTTON_POSITION,
  nativeOverlayWidth,
  normalizeTitleBarTheme,
  titleBarOverlayOptions,
  windowChromeOptions
} = require('./titlebar.cjs')

const PLATFORMS = ['darwin', 'win32', 'linux']

test('every OS gets the same frameless 34px title bar band', () => {
  for (const platform of PLATFORMS) {
    const chrome = windowChromeOptions(platform)

    assert.equal(chrome.titleBarStyle, 'hidden', platform)
    assert.equal(chrome.titleBarOverlay.height, TITLEBAR_HEIGHT, platform)
  }
})

test('macOS insets the traffic lights at the top left and reserves no right edge', () => {
  const chrome = windowChromeOptions('darwin')

  assert.deepEqual(chrome.trafficLightPosition, WINDOW_BUTTON_POSITION)
  assert.equal(chrome.vibrancy, 'sidebar')
  assert.deepEqual(titleBarOverlayOptions('darwin'), { height: TITLEBAR_HEIGHT })
  assert.equal(nativeOverlayWidth('darwin'), 0)
  // The traffic lights sit vertically centred in the band.
  assert.equal(WINDOW_BUTTON_POSITION.y * 2 + MACOS_TRAFFIC_LIGHTS_HEIGHT, TITLEBAR_HEIGHT)
})

test('Windows and Linux paint native controls on the right, tinted like the app', () => {
  for (const platform of ['win32', 'linux']) {
    const chrome = windowChromeOptions(platform)

    assert.equal(chrome.trafficLightPosition, undefined, platform)
    assert.equal(chrome.vibrancy, undefined, platform)
    assert.equal(nativeOverlayWidth(platform), NATIVE_OVERLAY_BUTTON_WIDTH, platform)
    assert.deepEqual(
      titleBarOverlayOptions(platform, { theme: { background: '#101820', foreground: '#f0f0f0' } }),
      { color: '#101820', height: TITLEBAR_HEIGHT, symbolColor: '#f0f0f0' },
      platform
    )
  }
})

test('the overlay falls back to the OS light/dark colours before the theme arrives', () => {
  assert.deepEqual(titleBarOverlayOptions('win32', { useDarkColors: true }), {
    color: '#111111',
    height: TITLEBAR_HEIGHT,
    symbolColor: '#f7f7f7'
  })
  assert.deepEqual(titleBarOverlayOptions('linux'), {
    color: '#f7f7f7',
    height: TITLEBAR_HEIGHT,
    symbolColor: '#242424'
  })
})

test('the shared chrome options hand back a copy of the traffic-light position', () => {
  const chrome = windowChromeOptions('darwin')

  chrome.trafficLightPosition.x = 99
  assert.equal(WINDOW_BUTTON_POSITION.x, 24)
})

test('normalizeTitleBarTheme accepts only #rrggbb pairs', () => {
  assert.deepEqual(normalizeTitleBarTheme({ background: '#000000', foreground: '#FFFFFF', extra: 1 }), {
    background: '#000000',
    foreground: '#FFFFFF'
  })
  assert.equal(normalizeTitleBarTheme({ background: 'red', foreground: '#ffffff' }), null)
  assert.equal(normalizeTitleBarTheme({ background: '#000' }), null)
  assert.equal(normalizeTitleBarTheme(null), null)
})
