/**
 * AIMDS-Suite Keycloak login helpers (pure, unit-testable) — the desktop twin
 * of hermes_cli/web_server.py `_iamds_keycloak_base_url`.
 *
 * Why the redirect guard exists (AIS-298, SUP-20260907-111120): the login
 * window shares `session.defaultSession`, so after the first successful login
 * Keycloak's SSO cookie is present and `/openid-connect/auth` answers the very
 * first request with a 302 to `hermes://callback?code=…`. Our `will-redirect`
 * handler cancels that redirect (the custom scheme is not registered; we do
 * the token exchange ourselves) — but a navigation cancelled during the
 * initial load makes `webContents.loadURL()` reject with
 * `ERR_FAILED (-2) loading '<auth url>'`. Treating that rejection as a login
 * failure destroyed the window and dropped the in-flight token exchange, so
 * every "re-authenticate" after the first login failed.
 */

const SUITE_URL_SUFFIXES = ['/litellm/v1', '/litellm', '/auth']

/** `https://host/litellm/v1`, `…/litellm`, `…/auth` or `…/` → `https://host`. */
function resolveSuiteRootDomain(baseUrl) {
  let root = String(baseUrl || '').trim().replace(/\/+$/, '')
  for (const suffix of SUITE_URL_SUFFIXES) {
    if (root.endsWith(suffix)) {
      root = root.slice(0, -suffix.length).replace(/\/+$/, '')
      break
    }
  }
  return root
}

/** True when `url` is the OAuth callback we intercept (scheme + host match, any query). */
function isKeycloakCallbackUrl(url, redirectUri) {
  const target = String(redirectUri || '').trim()
  const value = String(url || '')
  return Boolean(target) && value.startsWith(target)
}

/**
 * Decide whether a `loadURL()` rejection means the login failed.
 * Once the callback redirect has been intercepted (or the flow is already
 * settled) the rejection is the expected side effect of cancelling that
 * redirect and must not abort the token exchange.
 */
function shouldIgnoreLoginLoadFailure({ settled, redirectHandled }) {
  return Boolean(settled || redirectHandled)
}

module.exports = {
  SUITE_URL_SUFFIXES,
  isKeycloakCallbackUrl,
  resolveSuiteRootDomain,
  shouldIgnoreLoginLoadFailure
}
