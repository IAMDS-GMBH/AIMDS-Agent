import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hermes', () => ({}))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: { subscribe: () => () => undefined },
  startManualProviderOAuth: vi.fn(),
  startManualApiKeyEntry: vi.fn()
}))

import { normalizeProviderBaseUrl } from './providers-settings'

// AIS-599: http://staging.suite… answered 308 on every chat call.
describe('normalizeProviderBaseUrl', () => {
  it.each([
    ['http://staging.suite.iamds.com', 'https://staging.suite.iamds.com/litellm/v1'],
    ['staging.suite.iamds.com/litellm/mcp', 'https://staging.suite.iamds.com/litellm/v1'],
    ['https://suite.iamds.com/litellm/v1/', 'https://suite.iamds.com/litellm/v1'],
    ['http://localhost:8000', 'http://localhost:8000/litellm/v1'],
    ['http://192.168.1.20', 'http://192.168.1.20/litellm/v1'],
    ['http://suite-dev', 'http://suite-dev/litellm/v1']
  ])('%s → %s', (raw, expected) => {
    expect(normalizeProviderBaseUrl(raw)).toBe(expected)
  })
})
