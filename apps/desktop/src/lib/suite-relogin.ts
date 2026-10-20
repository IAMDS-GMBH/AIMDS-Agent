import { getAimdsSuiteStatus, keycloakLogin, storeAimdsSuiteKey, suiteKeyRejectionCode } from '@/hermes'
import { translateNow } from '@/i18n'
import { notify, notifyError } from '@/store/notifications'

const DEFAULT_REALM: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REALM ?? 'master'
const DEFAULT_REDIRECT_URI: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REDIRECT_URI ?? ''

export function suiteRootDomain(baseUrl: string): string {
  let root = baseUrl.trim().replace(/\/+$/, '')

  for (const suffix of ['/litellm/v1', '/litellm', '/auth']) {
    if (root.endsWith(suffix)) {
      root = root.slice(0, -suffix.length).replace(/\/+$/, '')
    }
  }

  return root
}

// AIS-599: the "sign-in required" notice starts the login for exactly that
// environment; the backend checks the new key before it stores it. Returns
// whether the environment is signed in again.
export async function signInToSuiteEnvironment(providerId: string): Promise<boolean> {
  const title = translateNow('settings.providers.suite.notifications.signInNow')

  try {
    const status = await getAimdsSuiteStatus()
    const env = status.environments.find(candidate => candidate.id === providerId)
    const root = suiteRootDomain(env?.base_url ?? '')

    if (!root) {
      notify({ kind: 'error', message: translateNow('settings.providers.suite.enterUrlFirst'), title })

      return false
    }

    const result = await keycloakLogin({
      baseUrl: root,
      forceLogin: true,
      realm: DEFAULT_REALM,
      redirectUri: DEFAULT_REDIRECT_URI || 'hermes://callback'
    })

    await storeAimdsSuiteKey(providerId, result.apiKey, root)
    notify({ kind: 'success', message: translateNow('settings.providers.suite.connected'), title })

    return true
  } catch (err) {
    const rejected = suiteKeyRejectionCode(err)

    if (rejected) {
      notify({
        kind: 'error',
        message: translateNow(
          rejected === 'wrong_environment' ? 'settings.providers.suite.keyWrongEnvironment' : 'settings.providers.suite.keyRejected'
        ),
        title
      })
    } else {
      notifyError(err, 'Keycloak SSO failed')
    }

    return false
  }
}
