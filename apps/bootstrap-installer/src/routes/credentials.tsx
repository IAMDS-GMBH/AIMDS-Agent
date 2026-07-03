import { useMemo, useState } from 'react'
import { Button } from '../components/button'
import { joinInstallerBaseUrl, normalizeInstallerBaseUrl, startInstall } from '../store'
import { AlertCircle, Loader, Check } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'

type EndpointVariant = 'dev' | 'main' | 'staging'

export interface CredentialsData {
  apiKey: string
  baseUrl: string
  modelName: string
  modelNames?: string[]
  selectedEndpoint?: EndpointVariant
  stagingApiKey?: string
  devApiKey?: string
  stagingBaseUrl?: string
  devBaseUrl?: string
}

interface VariantUrls {
  dev: string
  main: string
  staging: string
}

const DEV_UNLOCK_CLICKS = 10

function buildVariantBaseUrls(baseUrl: string): VariantUrls {
  const normalized = normalizeInstallerBaseUrl(baseUrl)
  if (!normalized) {
    return { dev: '', main: '', staging: '' }
  }
  try {
    const parsed = new URL(normalized)
    const rootHost = parsed.hostname.replace(/^(staging|dev)\./i, '')
    const buildForHost = (host: string) => {
      const next = new URL(normalized)
      next.hostname = host
      return normalizeInstallerBaseUrl(next.toString())
    }
    return {
      main: buildForHost(rootHost),
      staging: buildForHost(`staging.${rootHost}`),
      dev: buildForHost(`dev.${rootHost}`)
    }
  } catch {
    return { dev: '', main: '', staging: '' }
  }
}

function computeFetchFingerprint(formData: CredentialsData, variantsEnabled: boolean): string {
  const variants = buildVariantBaseUrls(formData.baseUrl)
  const selected = variantsEnabled ? (formData.selectedEndpoint ?? 'staging') : 'main'
  return [
    selected,
    variants.main,
    variants.staging,
    variants.dev,
    formData.apiKey.trim(),
    (formData.stagingApiKey ?? '').trim(),
    (formData.devApiKey ?? '').trim()
  ].join('|')
}

export default function Credentials() {
  const [formData, setFormData] = useState<CredentialsData>({
    apiKey: '',
    baseUrl: '',
    modelName: '',
    selectedEndpoint: 'staging',
    stagingApiKey: '',
    devApiKey: ''
  })

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [modelsFetched, setModelsFetched] = useState(false)
  const [fetchedFingerprint, setFetchedFingerprint] = useState('')
  const [modelError, setModelError] = useState<string | null>(null)
  const [providerLabelClicks, setProviderLabelClicks] = useState(0)
  const [variantsEnabled, setVariantsEnabled] = useState(false)

  const variantUrls = useMemo(() => buildVariantBaseUrls(formData.baseUrl), [formData.baseUrl])
  const selectedEndpoint: EndpointVariant = variantsEnabled ? (formData.selectedEndpoint ?? 'staging') : 'main'
  const selectedApiKey =
    selectedEndpoint === 'main'
      ? formData.apiKey
      : selectedEndpoint === 'staging'
        ? (formData.stagingApiKey ?? '')
        : (formData.devApiKey ?? '')
  const selectedBaseUrl = variantUrls[selectedEndpoint]
  const selectedEndpointLabel = selectedEndpoint === 'main' ? 'Main' : selectedEndpoint === 'staging' ? 'Staging' : 'Dev'

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

    if (variantsEnabled) {
      if (!selectedApiKey.trim()) {
        newErrors[selectedEndpoint === 'main' ? 'apiKey' : selectedEndpoint === 'staging' ? 'stagingApiKey' : 'devApiKey'] =
          `${selectedEndpointLabel} API Key is required`
      }
    } else if (!formData.apiKey.trim()) {
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
    const currentFingerprint = computeFetchFingerprint(formData, variantsEnabled)
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

    if (variantsEnabled) {
      cleaned.selectedEndpoint = selectedEndpoint
      cleaned.stagingApiKey = (formData.stagingApiKey ?? '').trim()
      cleaned.devApiKey = (formData.devApiKey ?? '').trim()
      cleaned.stagingBaseUrl = variantUrls.staging
      cleaned.devBaseUrl = variantUrls.dev
    }

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
      const previousFingerprint = computeFetchFingerprint(previous, variantsEnabled)
      const nextFingerprint = computeFetchFingerprint(next, variantsEnabled)
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

  const handleProviderLabelClick = () => {
    const next = providerLabelClicks + 1
    setProviderLabelClicks(next)
    if (!variantsEnabled && next >= DEV_UNLOCK_CLICKS) {
      setVariantsEnabled(true)
      setFormData((prev) => ({ ...prev, selectedEndpoint: 'staging' }))
      if (modelsFetched) {
        invalidateFetchedModels()
      }
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
        setModelError(`${selectedEndpointLabel} API Key is required`)
        setIsLoadingModels(false)
        return
      }

      const models = await invoke<string[]>('fetch_models', {
        baseUrl: selectedBaseUrl,
        apiKey: selectedApiKey
      })

      setAvailableModels(models)
      setModelsFetched(true)
      setFetchedFingerprint(computeFetchFingerprint(formData, variantsEnabled))
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

  return (
    <div className="hermes-fade-in flex h-full flex-col overflow-auto bg-background px-8 py-10">
      <div className="mx-auto w-full max-w-xl">
        <h1 className="mb-2 text-2xl font-semibold text-foreground">
          Configuration
        </h1>
        <p className="mb-8 text-sm text-muted-foreground">
          Enter your KI provider credentials. The installer will derive the endpoints from your Base URL.
        </p>

        <form onSubmit={handleSubmit} className="space-y-6">
          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-4 block text-sm font-medium text-foreground">
              <button
                type="button"
                onClick={handleProviderLabelClick}
                className="cursor-default"
              >
                KI Provider
              </button>
            </legend>

            <div className="space-y-4">
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
                    if (normalized) {
                      handleChange('baseUrl', normalized)
                    }
                  }}
                  placeholder="https://suite.example.com"
                  className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Uses {joinInstallerBaseUrl(selectedBaseUrl || formData.baseUrl, 'litellm/v1') || 'https://BASE_URL/litellm/v1'} for LLM and {joinInstallerBaseUrl(selectedBaseUrl || formData.baseUrl, 'litellm/mcp') || 'https://BASE_URL/litellm/mcp'} for MCP.
                </p>
                {errors.baseUrl && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.baseUrl}
                  </p>
                )}
              </div>

              {variantsEnabled ? (
                <>
                  <div className="space-y-2 rounded border border-border bg-background/70 p-3">
                    <p className="text-xs font-medium text-foreground">Endpoint selection</p>
                    {(['main', 'staging', 'dev'] as EndpointVariant[]).map((endpoint) => {
                      const label = endpoint === 'main' ? 'Main' : endpoint === 'staging' ? 'Staging' : 'Dev'
                      const url = variantUrls[endpoint]
                      return (
                        <label key={endpoint} className="flex items-start gap-2 text-xs text-foreground">
                          <input
                            type="radio"
                            name="selectedEndpoint"
                            checked={selectedEndpoint === endpoint}
                            onChange={() => handleChange('selectedEndpoint', endpoint)}
                            className="mt-0.5"
                          />
                          <span>
                            <span className="block font-medium">{label}</span>
                            <span className="text-muted-foreground">{url || 'Invalid base URL'}</span>
                          </span>
                        </label>
                      )
                    })}
                  </div>

                  <div>
                    <label htmlFor="apiKeyMain" className="block text-sm font-medium text-foreground">
                      Main API Key
                    </label>
                    <input
                      id="apiKeyMain"
                      type="password"
                      value={formData.apiKey}
                      onChange={(e) => handleChange('apiKey', e.target.value)}
                      placeholder="sk-..."
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
                    <label htmlFor="apiKeyStaging" className="block text-sm font-medium text-foreground">
                      Staging API Key
                    </label>
                    <input
                      id="apiKeyStaging"
                      type="password"
                      value={formData.stagingApiKey ?? ''}
                      onChange={(e) => handleChange('stagingApiKey', e.target.value)}
                      placeholder="sk-..."
                      className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    {errors.stagingApiKey && (
                      <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                        <AlertCircle className="h-3 w-3" />
                        {errors.stagingApiKey}
                      </p>
                    )}
                  </div>

                  <div>
                    <label htmlFor="apiKeyDev" className="block text-sm font-medium text-foreground">
                      Dev API Key
                    </label>
                    <input
                      id="apiKeyDev"
                      type="password"
                      value={formData.devApiKey ?? ''}
                      onChange={(e) => handleChange('devApiKey', e.target.value)}
                      placeholder="sk-..."
                      className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    {errors.devApiKey && (
                      <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                        <AlertCircle className="h-3 w-3" />
                        {errors.devApiKey}
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <div>
                  <label htmlFor="apiKey" className="block text-sm font-medium text-foreground">
                    API Key <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="apiKey"
                    type="password"
                    value={formData.apiKey}
                    onChange={(e) => handleChange('apiKey', e.target.value)}
                    placeholder="sk-..."
                    className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  {errors.apiKey && (
                    <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                      <AlertCircle className="h-3 w-3" />
                      {errors.apiKey}
                    </p>
                  )}
                </div>
              )}

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
                      `Fetch Models (${selectedEndpointLabel})`
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
                    If endpoint selection, Base URL, or any API key changes, fetch models again before install.
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
