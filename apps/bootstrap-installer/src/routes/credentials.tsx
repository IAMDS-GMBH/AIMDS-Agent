import { useState } from 'react'
import { Button } from '../components/button'
import { joinInstallerBaseUrl, normalizeInstallerBaseUrl, startInstall } from '../store'
import { AlertCircle, Loader, Check, ShieldCheck } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'

type EndpointVariant = 'dev' | 'main' | 'staging'

// Enterprise defaults baked in at packaging time (see vite.config.ts).
const DEFAULT_BASE_URL: string = import.meta.env.VITE_DEFAULT_BASE_URL ?? ''
const DEFAULT_REALM: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REALM ?? 'aimds'
const DEFAULT_REDIRECT_URI: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REDIRECT_URI ?? ''

interface KeycloakLoginResult {
  api_key: string
  base_url: string
}

export interface CredentialsData {
  apiKey: string
  baseUrl: string
  modelName: string
  modelNames?: string[]
  selectedEndpoint?: EndpointVariant
}

function computeFetchFingerprint(formData: CredentialsData): string {
  const mainBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)
  return [
    'main',
    mainBaseUrl,
    formData.apiKey.trim()
  ].join('|')
}

export default function Credentials() {
  const [formData, setFormData] = useState<CredentialsData>({
    apiKey: '',
    baseUrl: DEFAULT_BASE_URL,
    modelName: '',
    selectedEndpoint: 'main'
  })

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [modelsFetched, setModelsFetched] = useState(false)
  const [fetchedFingerprint, setFetchedFingerprint] = useState('')
  const [modelError, setModelError] = useState<string | null>(null)
  const [isKeycloakLoading, setIsKeycloakLoading] = useState(false)
  const [keycloakError, setKeycloakError] = useState<string | null>(null)
  const [keycloakConnected, setKeycloakConnected] = useState(false)
  const selectedEndpoint: EndpointVariant = 'main'
  const selectedApiKey = formData.apiKey
  const selectedBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)

  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {}

    if (!formData.baseUrl.trim()) {
      newErrors.baseUrl = 'Base URL is required'
    } else if (!normalizeInstallerBaseUrl(formData.baseUrl)) {
      newErrors.baseUrl = 'Base URL is invalid'
    }

    if (!formData.modelName.trim()) {
      newErrors.modelName = 'Model name is required'
    }

    if (!formData.apiKey.trim()) {
      newErrors.apiKey = 'API Key is required'
    }
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validateForm()) return
    if (!modelsFetched) {
      setErrors({ ...errors, modelName: 'Please fetch and select a model first' })
      return
    }
    const currentFingerprint = computeFetchFingerprint(formData)
    if (currentFingerprint !== fetchedFingerprint) {
      setModelsFetched(false)
      setErrors({ ...errors, modelName: 'Inputs changed. Please fetch models again' })
      return
    }

    const normalizedBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)
    if (!normalizedBaseUrl) {
      setErrors({ ...errors, baseUrl: 'Base URL is invalid' })
      return
    }

    const cleaned: CredentialsData = {
      apiKey: formData.apiKey.trim(),
      baseUrl: normalizedBaseUrl,
      modelName: formData.modelName.trim(),
      modelNames: [...availableModels]
    }
    cleaned.selectedEndpoint = selectedEndpoint

    await startInstall({ credentials: cleaned })
  }

  const invalidateFetchedModels = () => {
    setModelsFetched(false)
    setAvailableModels([])
    setFetchedFingerprint('')
    setFormData((prev) => ({ ...prev, modelName: '' }))
  }

  const handleChange = (field: keyof CredentialsData, value: string) => {
    const previous = formData
    setFormData((prev) => ({ ...prev, [field]: value }))

    if (modelsFetched) {
      const next = { ...previous, [field]: value }
      const previousFingerprint = computeFetchFingerprint(previous)
      const nextFingerprint = computeFetchFingerprint(next)
      if (previousFingerprint !== nextFingerprint) {
        invalidateFetchedModels()
      }
    }

    if (errors[field]) {
      setErrors((prev) => {
        const next = { ...prev }
        delete next[field]
        return next
      })
    }
  }

  const handleFetchModels = async () => {
    setModelError(null)
    setIsLoadingModels(true)

    try {
      if (!formData.baseUrl.trim()) {
        setModelError('Base URL is required')
        setIsLoadingModels(false)
        return
      }
      if (!selectedBaseUrl) {
        setModelError('Base URL is invalid')
        setIsLoadingModels(false)
        return
      }
      if (!selectedApiKey.trim()) {
        setModelError('API Key is required')
        setIsLoadingModels(false)
        return
      }

      const models = await invoke<string[]>('fetch_models', {
        baseUrl: selectedBaseUrl,
        apiKey: selectedApiKey
      })

      setAvailableModels(models)
      setModelsFetched(true)
      setFetchedFingerprint(computeFetchFingerprint(formData))
      setFormData((prev) => ({ ...prev, modelName: models[0] }))

      try {
        await invoke('write_provider_models_cache', {
          hermes_home: null,
          model_names: models
        })
      } catch (cacheError) {
        console.warn('Failed to write model cache:', cacheError)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.error('Model fetch error:', message, error)
      setModelError(message)
      setAvailableModels([])
      setModelsFetched(false)
    } finally {
      setIsLoadingModels(false)
    }
  }

  const handleKeycloakLogin = async () => {
    setKeycloakError(null)
    setIsKeycloakLoading(true)
    setKeycloakConnected(false)

    const baseUrl = normalizeInstallerBaseUrl(formData.baseUrl)
    if (!baseUrl) {
      setKeycloakError('Enter a valid Base URL before connecting with Keycloak')
      setIsKeycloakLoading(false)
      return
    }

    try {
      const result = await invoke<KeycloakLoginResult>('keycloak_login', {
        baseUrl,
        realm: DEFAULT_REALM,
        redirectUri: DEFAULT_REDIRECT_URI || `${baseUrl}/oauth/oidc/callback`
      })

      // Populate the form with the SSO-obtained key so the user can proceed
      setFormData((prev) => ({
        ...prev,
        apiKey: result.api_key,
        baseUrl: result.base_url || prev.baseUrl,
      }))
      setKeycloakConnected(true)
      // Invalidate any previously fetched models since the key changed
      invalidateFetchedModels()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.error('Keycloak login error:', message)
      setKeycloakError(message)
    } finally {
      setIsKeycloakLoading(false)
    }
  }

  return (
    <div className="hermes-fade-in flex h-full flex-col overflow-auto bg-background px-8 py-10">
      <div className="mx-auto w-full max-w-xl">
        <h1 className="mb-2 text-2xl font-semibold text-foreground">
          Configuration
        </h1>
        <p className="mb-8 text-sm text-muted-foreground">
          Enter your KI provider credentials.
        </p>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Keycloak SSO — primary path when DEFAULT_BASE_URL is set */}
          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-3 block text-sm font-medium text-foreground">
              Single Sign-On
            </legend>

            <div className="space-y-3">
              <div>
                <label htmlFor="baseUrl" className="block text-sm font-medium text-foreground">
                  Base URL <span className="text-red-500">*</span>
                </label>
                <input
                  id="baseUrl"
                  type="text"
                  value={formData.baseUrl}
                  onChange={(e) => handleChange('baseUrl', e.target.value)}
                  onBlur={() => {
                    const normalized = normalizeInstallerBaseUrl(formData.baseUrl)
                    if (normalized) handleChange('baseUrl', normalized)
                  }}
                  placeholder="https://suite.example.com"
                  className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Uses {joinInstallerBaseUrl(selectedBaseUrl || formData.baseUrl, 'litellm/v1') || 'https://BASE_URL/litellm/v1'} for LLM.
                </p>
                {errors.baseUrl && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.baseUrl}
                  </p>
                )}
              </div>

              <button
                type="button"
                onClick={() => void handleKeycloakLogin()}
                disabled={isKeycloakLoading || !formData.baseUrl.trim()}
                className="flex w-full items-center justify-center gap-2 rounded border border-primary bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
              >
                {isKeycloakLoading ? (
                  <>
                    <Loader className="h-4 w-4 animate-spin" />
                    Connecting to Keycloak…
                  </>
                ) : keycloakConnected ? (
                  <>
                    <Check className="h-4 w-4 text-green-500" />
                    Connected via Keycloak
                  </>
                ) : (
                  <>
                    <ShieldCheck className="h-4 w-4" />
                    Connect with Keycloak
                  </>
                )}
              </button>

              {keycloakConnected && (
                <p className="text-xs text-green-600 dark:text-green-400">
                  API key obtained via SSO. Fetch models below and proceed to install.
                </p>
              )}
              {keycloakError && (
                <p className="flex items-center gap-1 text-xs text-red-500">
                  <AlertCircle className="h-3 w-3" />
                  {keycloakError}
                </p>
              )}
            </div>
          </fieldset>

          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-4 block text-sm font-medium text-foreground">
              KI Provider
            </legend>

            <div className="space-y-4">
              <div>
                <label htmlFor="apiKey" className="block text-sm font-medium text-foreground">
                  API Key <span className="text-red-500">*</span>
                </label>
                <input
                  id="apiKey"
                  type="password"
                  value={formData.apiKey}
                  onChange={(e) => handleChange('apiKey', e.target.value)}
                  placeholder={keycloakConnected ? '(obtained via Keycloak SSO)' : 'sk-…'}
                  className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
                {errors.apiKey && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.apiKey}
                  </p>
                )}
              </div>

              <div>
                <label htmlFor="modelName" className="block text-sm font-medium text-foreground">
                  Model Name <span className="text-red-500">*</span>
                </label>
                {modelsFetched ? (
                  <select
                    id="modelName"
                    value={formData.modelName}
                    onChange={(e) => handleChange('modelName', e.target.value)}
                    className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    {availableModels.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="modelName"
                    type="text"
                    value=""
                    placeholder="Click 'Fetch Models' first"
                    disabled
                    className="mt-1 w-full rounded border border-input bg-muted px-3 py-2 text-sm text-muted-foreground disabled:opacity-50 disabled:cursor-not-allowed"
                  />
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={handleFetchModels}
                    disabled={isLoadingModels || !selectedBaseUrl || !selectedApiKey.trim()}
                    className="flex items-center gap-2 rounded border border-input bg-muted/50 px-3 py-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isLoadingModels ? (
                      <>
                        <Loader className="h-3 w-3 animate-spin" />
                        Fetching...
                      </>
                    ) : modelsFetched ? (
                      <>
                        <Check className="h-3 w-3 text-green-500" />
                        Fetched ({availableModels.length} models)
                      </>
                    ) : (
                      'Fetch Models'
                    )}
                  </button>
                </div>
                {modelError && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {modelError}
                  </p>
                )}
                {errors.modelName && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.modelName}
                  </p>
                )}
                {!modelsFetched && availableModels.length === 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    If Base URL or API key changes, fetch models again before install.
                  </p>
                )}
              </div>
            </div>
          </fieldset>

          <div className="flex justify-end gap-3 pt-4">
            <Button
              type="submit"
              size="lg"
              className="min-w-32"
              disabled={!modelsFetched}
            >
              Install Hermes
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

