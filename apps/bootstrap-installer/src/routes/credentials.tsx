import { useState } from 'react'
import { Button } from '../components/button'
import { joinInstallerBaseUrl, normalizeInstallerBaseUrl, startInstall } from '../store'
import { AlertCircle, Loader, Check } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'

export interface CredentialsData {
  apiKey: string
  baseUrl: string
  modelName: string
  modelNames?: string[]
}

export default function Credentials() {
  const [formData, setFormData] = useState<CredentialsData>({
    apiKey: '',
    baseUrl: '',
    modelName: ''
  })

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [modelsFetched, setModelsFetched] = useState(false)
  const [modelError, setModelError] = useState<string | null>(null)

  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {}

    if (!formData.apiKey.trim()) {
      newErrors.apiKey = 'API Key is required'
    }
    if (!formData.baseUrl.trim()) {
      newErrors.baseUrl = 'Base URL is required'
    } else if (!normalizeInstallerBaseUrl(formData.baseUrl)) {
      newErrors.baseUrl = 'Base URL is invalid'
    }
    if (!formData.modelName.trim()) {
      newErrors.modelName = 'Model name is required'
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

    // Clean up optional empty fields
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

    await startInstall({ credentials: cleaned })
  }

  const handleChange = (field: keyof CredentialsData, value: string) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
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
      const normalizedBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)
      if (!normalizedBaseUrl) {
        setModelError('Base URL is invalid')
        setIsLoadingModels(false)
        return
      }
      if (!formData.apiKey.trim()) {
        setModelError('API Key is required')
        setIsLoadingModels(false)
        return
      }

      const models = await invoke<string[]>('fetch_models', {
        baseUrl: normalizedBaseUrl,
        apiKey: formData.apiKey
      })
      
      setAvailableModels(models)
      setModelsFetched(true)
      setFormData((prev) => ({ ...prev, modelName: models[0] }))
      
      // Write provider models cache for later use during install
      try {
        await invoke('write_provider_models_cache', {
          hermes_home: null, // Use platform default (LOCALAPPDATA\hermes on Windows, ~/.hermes elsewhere)
          model_names: models
        })
      } catch (cacheError) {
        console.warn('Failed to write model cache:', cacheError)
        // Don't block on cache write failure; proceed anyway
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
          {/* KI Provider Section */}
          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-4 block text-sm font-medium text-foreground">
              KI Provider
            </legend>

            <div className="space-y-4">
              {/* Base URL */}
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
                  Uses {joinInstallerBaseUrl(formData.baseUrl, 'litellm/v1') || 'https://BASE_URL/litellm/v1'} for LLM and {joinInstallerBaseUrl(formData.baseUrl, 'litellm/mcp') || 'https://BASE_URL/litellm/mcp'} for MCP.
                </p>
                {errors.baseUrl && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.baseUrl}
                  </p>
                )}
              </div>

              {/* API Key */}
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

              {/* Model Name */}
              <div>
                <label htmlFor="modelName" className="block text-sm font-medium text-foreground">
                  Model Name <span className="text-red-500">*</span>
                </label>
                {modelsFetched ? (
                  // Dropdown when models are fetched
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
                  // Disabled input when models not yet fetched
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
                    disabled={isLoadingModels || !formData.baseUrl.trim() || !formData.apiKey.trim()}
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
              </div>
            </div>
          </fieldset>

          {/* Form Actions */}
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
