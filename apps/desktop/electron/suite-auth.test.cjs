/**
 * Tests for electron/suite-auth.cjs — pure helpers behind the AIMDS-Suite
 * Keycloak login window (AIS-298).
 *
 * Run with: node --test electron/suite-auth.test.cjs
 */

const test = require('node:test')
const assert = require('node:assert/strict')

const {
  isKeycloakCallbackUrl,
  resolveSuiteRootDomain,
  shouldIgnoreLoginLoadFailure
} = require('./suite-auth.cjs')

test('resolveSuiteRootDomain strips exactly one Suite suffix, like the Python twin', () => {
  assert.equal(resolveSuiteRootDomain('https://suite.iamds.com/litellm/v1'), 'https://suite.iamds.com')
  assert.equal(resolveSuiteRootDomain('https://suite.iamds.com/litellm/v1/'), 'https://suite.iamds.com')
  assert.equal(resolveSuiteRootDomain('https://staging.suite.iamds.com/litellm'), 'https://staging.suite.iamds.com')
  assert.equal(resolveSuiteRootDomain('https://suite.iamds.com/auth'), 'https://suite.iamds.com')
  assert.equal(resolveSuiteRootDomain('  https://suite.iamds.com  '), 'https://suite.iamds.com')
  // Only the last suffix is stripped (break parity with web_server.py).
  assert.equal(resolveSuiteRootDomain('https://suite.iamds.com/auth/litellm'), 'https://suite.iamds.com/auth')
  assert.equal(resolveSuiteRootDomain(''), '')
})

test('isKeycloakCallbackUrl matches the configured redirect URI with any query', () => {
  assert.equal(isKeycloakCallbackUrl('hermes://callback?code=abc&session_state=x', 'hermes://callback'), true)
  assert.equal(isKeycloakCallbackUrl('hermes://callback', 'hermes://callback'), true)
  assert.equal(isKeycloakCallbackUrl('https://suite.iamds.com/auth/realms/aimds/login-actions/authenticate', 'hermes://callback'), false)
  assert.equal(isKeycloakCallbackUrl('hermes://callback?code=abc', ''), false)
})

test('shouldIgnoreLoginLoadFailure: a cancelled SSO redirect is not a login failure', () => {
  // Fresh window, page really failed to load → surface it.
  assert.equal(shouldIgnoreLoginLoadFailure({ settled: false, redirectHandled: false }), false)
  // SSO cookie present: 302 → hermes://callback intercepted during the initial
  // load; loadURL rejects with ERR_FAILED (-2) — the token exchange is running.
  assert.equal(shouldIgnoreLoginLoadFailure({ settled: false, redirectHandled: true }), true)
  // Already resolved/rejected → nothing left to report.
  assert.equal(shouldIgnoreLoginLoadFailure({ settled: true, redirectHandled: false }), true)
})
