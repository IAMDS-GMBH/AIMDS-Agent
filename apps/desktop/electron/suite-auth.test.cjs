/**
 * Tests for electron/suite-auth.cjs — pure helpers behind the AIMDS-Suite
 * Keycloak login window (AIS-298).
 *
 * Run with: node --test electron/suite-auth.test.cjs
 */

const test = require('node:test')
const assert = require('node:assert/strict')

const {
  buildKeycloakAuthUrl,
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

test('buildKeycloakAuthUrl: prompt=login only when a fresh login is forced (AIS-348)', () => {
  const base = {
    authBaseUrl: 'https://suite.iamds.com/auth',
    realm: 'aimds',
    redirectUri: 'hermes://callback',
    codeChallenge: 'chal'
  }
  const plain = new URL(buildKeycloakAuthUrl(base))
  assert.equal(plain.origin + plain.pathname, 'https://suite.iamds.com/auth/realms/aimds/protocol/openid-connect/auth')
  assert.equal(plain.searchParams.get('client_id'), 'hermes-app')
  assert.equal(plain.searchParams.get('response_type'), 'code')
  assert.equal(plain.searchParams.get('scope'), 'openid')
  assert.equal(plain.searchParams.get('redirect_uri'), 'hermes://callback')
  assert.equal(plain.searchParams.get('code_challenge'), 'chal')
  assert.equal(plain.searchParams.get('code_challenge_method'), 'S256')
  assert.equal(plain.searchParams.get('prompt'), null)

  // "Re-authenticate" on a Suite provider row: Keycloak must show the
  // credentials form even while its SSO cookie is alive.
  const forced = new URL(buildKeycloakAuthUrl({ ...base, forceLogin: true }))
  assert.equal(forced.searchParams.get('prompt'), 'login')
  assert.equal(forced.searchParams.get('code_challenge'), 'chal')
  assert.equal(forced.searchParams.get('redirect_uri'), 'hermes://callback')
})
