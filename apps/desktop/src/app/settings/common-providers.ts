// The short list of providers the Accounts page offers for setup (AIS-325).
// Everything else the backend OAuth catalog knows (Nous, Qwen, MiniMax, xAI …)
// stays reachable through the onboarding overlay's "Other providers"
// disclosure, but is deliberately not advertised here.
export type CommonProviderKind = 'apikey' | 'custom' | 'oauth'

export interface CommonProvider {
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
  { id: 'custom', kind: 'custom', name: 'Custom endpoint', envKey: 'OPENAI_BASE_URL' }
]

/** OAuth catalog ids the onboarding overlay shows up front (rest collapsed). */
export const FEATURED_OAUTH_IDS: readonly string[] = COMMON_PROVIDERS.filter(p => p.oauthId).map(
  p => p.oauthId as string
)
