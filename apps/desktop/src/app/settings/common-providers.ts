// The short list of providers the Accounts page offers for setup (AIS-325).
// Everything else the backend OAuth catalog knows (Nous, Qwen, MiniMax, xAI …)
// stays reachable through the onboarding overlay's "Other providers"
// disclosure, but is deliberately not advertised here.
export type CommonProviderKind = 'apikey' | 'custom' | 'oauth'

export interface CommonProvider {
  /**
   * Accounts page only: not an LLM provider, so the onboarding overlay's
   * featured list (model choice) must not advertise it.
   */
  accountsOnly?: boolean
  /** Env var carrying the credential — API-key and custom-endpoint rows. */
  envKey?: string
  id: string
  kind: CommonProviderKind
  /** Fallback label when the backend catalog has no entry for `oauthId`. */
  name: string
  /** Catalog id in `/api/providers/oauth` — OAuth rows only. */
  oauthId?: string
}

export const COMMON_PROVIDERS: readonly CommonProvider[] = [
  { id: 'anthropic', kind: 'oauth', name: 'Anthropic (OAuth)', oauthId: 'anthropic' },
  { id: 'gemini', kind: 'oauth', name: 'Google Gemini (OAuth)', oauthId: 'google-gemini-cli' },
  { id: 'openrouter', kind: 'apikey', name: 'OpenRouter', envKey: 'OPENROUTER_API_KEY' },
  { id: 'groq', kind: 'apikey', name: 'Groq', envKey: 'GROQ_API_KEY' },
  { id: 'custom', kind: 'custom', name: 'Custom endpoint', envKey: 'OPENAI_BASE_URL' },
  // Microsoft 365 is an account (mail, calendar, Teams via MSOffice365MCP),
  // not a model provider — it belongs on the Accounts page (with its tenant
  // consent control) but not in the onboarding model picker.
  { accountsOnly: true, id: 'microsoft', kind: 'oauth', name: 'Microsoft 365 (OAuth)', oauthId: 'microsoft' }
]

/** OAuth catalog ids the onboarding overlay shows up front (rest collapsed). */
export const FEATURED_OAUTH_IDS: readonly string[] = COMMON_PROVIDERS.filter(
  p => p.oauthId && !p.accountsOnly
).map(p => p.oauthId as string)
