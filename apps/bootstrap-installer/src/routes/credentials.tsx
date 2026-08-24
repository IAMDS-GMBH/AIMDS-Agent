import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from '../components/button'
import { ReportIssueDialog } from '../components/report-issue-dialog'
import { normalizeInstallerBaseUrl, startInstall } from '../store'
import { AlertCircle, Loader, Check, ShieldCheck, X, RefreshCw, LifeBuoy } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'

type EndpointVariant = 'dev' | 'main' | 'staging'

// Enterprise defaults baked in at packaging time (see vite.config.ts).
const DEFAULT_BASE_URL: string = import.meta.env.VITE_DEFAULT_BASE_URL ?? ''
const DEFAULT_REALM: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_REALM ?? 'aimds'
const DEFAULT_CLIENT_ID: string = import.meta.env.VITE_DEFAULT_KEYCLOAK_CLIENT_ID ?? 'hermes-app'

interface KeycloakLoginResult {
  api_key: string
  base_url: string
}

interface ExistingConfig {
  base_url?: string | null
  api_key?: string | null
  model?: string | null
}

export interface CredentialsData {
  apiKey: string
  baseUrl: string
  modelName: string
  modelNames?: string[]
  selectedEndpoint?: EndpointVariant
  stagingApiKey?: string
  devApiKey?: string
}

function computeFetchFingerprint(formData: CredentialsData): string {
  const mainBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)
  return [
    'main',
    mainBaseUrl,
    formData.apiKey.trim()
  ].join('|')
}

function LiteLLMHealthBadge({
  checking,
  healthy,
  onRecheck,
}: {
  checking: boolean
  healthy: boolean | null
  onRecheck: () => void
}) {
  return (
    <button
      type="button"
      onClick={onRecheck}
      disabled={checking}
      className={[
        'mt-2 inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors',
        checking
          ? 'bg-muted text-muted-foreground'
          : healthy === true
            ? 'bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400'
            : healthy === false
              ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/30 dark:text-red-400'
              : 'bg-muted text-muted-foreground hover:bg-muted/70',
      ].join(' ')}
    >
      {checking ? (
        <><Loader className="h-3 w-3 animate-spin" /> Checking LiteLLM…</>
      ) : healthy === true ? (
        <><Check className="h-3 w-3" /> LiteLLM reachable</>
      ) : healthy === false ? (
        <><X className="h-3 w-3" /> LiteLLM unreachable</>
      ) : (
        <><RefreshCw className="h-3 w-3" /> Check LiteLLM</>
      )}
    </button>
  )
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

  // Existing config discovery & adoption state
  const [existingConfig, setExistingConfig] = useState<ExistingConfig | null>(null)
  const [dismissedExisting, setDismissedExisting] = useState(false)

  const selectedEndpoint: EndpointVariant = 'main'
  const selectedApiKey = formData.apiKey
  const selectedBaseUrl = normalizeInstallerBaseUrl(formData.baseUrl)

  // LiteLLM health state: null = unchecked, true = healthy, false = unreachable
  const [litellmHealth, setLitellmHealth] = useState<boolean | null>(null)
  const [isCheckingHealth, setIsCheckingHealth] = useState(false)
  const [reportDialogOpen, setReportDialogOpen] = useState(false)
  const healthDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const checkHealth = useCallback(async (url: string) => {
    if (!url.trim()) return
    setIsCheckingHealth(true)
    try {
      const ok = await invoke<boolean>('check_litellm_health', { baseUrl: url })
      setLitellmHealth(ok)
    } catch {
      setLitellmHealth(false)
    } finally {
      setIsCheckingHealth(false)
    }
  }, [])

  // Check for existing configuration on mount
  useEffect(() => {
    async function loadExisting() {
      try {
        const found = await invoke<ExistingConfig>('get_existing_config', { hermesHome: null })
        if (found && (found.api_key || found.base_url || found.model)) {
          setExistingConfig(found)
        }
      } catch (err) {
        console.warn('Could not read existing config:', err)
      }
    }
    void loadExisting()
  }, [])

  // Auto-check on mount and whenever selectedBaseUrl changes (debounced for editable field)
  useEffect(() => {
    if (!selectedBaseUrl) return
    if (healthDebounceRef.current) clearTimeout(healthDebounceRef.current)
    healthDebounceRef.current = setTimeout(() => checkHealth(selectedBaseUrl), 600)
    return () => {
      if (healthDebounceRef.current) clearTimeout(healthDebounceRef.current)
    }
  }, [selectedBaseUrl, checkHealth])

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

  // Accepts optional overrides so callers that just obtained fresh
  // credentials (e.g. handleKeycloakLogin, right after its own setFormData)
  // can fetch with those values immediately instead of the stale `formData`
  // this closure captured at render time -- setFormData's update isn't
  // visible to this closure until the next render.
  const handleFetchModels = async (overrideBaseUrl?: string, overrideApiKey?: string) => {
    const baseUrlToUse = overrideBaseUrl ?? selectedBaseUrl
    const apiKeyToUse = overrideApiKey ?? selectedApiKey

    setModelError(null)
    setIsLoadingModels(true)

    try {
      if (!baseUrlToUse.trim()) {
        setModelError('Base URL is required')
        setIsLoadingModels(false)
        return
      }
      if (!apiKeyToUse.trim()) {
        setModelError('API Key is required')
        setIsLoadingModels(false)
        return
      }

      const models = await invoke<string[]>('fetch_models', {
        baseUrl: baseUrlToUse,
        apiKey: apiKeyToUse
      })

      setAvailableModels(models)
      setModelsFetched(true)
      setFetchedFingerprint(computeFetchFingerprint({ ...formData, baseUrl: baseUrlToUse, apiKey: apiKeyToUse }))
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
        clientId: DEFAULT_CLIENT_ID,
        redirectUri: 'hermes://callback',
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

      // Auto-fetch models now that we have a fresh key -- normal end users
      // otherwise land on this screen post-login with an empty model
      // dropdown and no indication they still need to press "Fetch
      // models" themselves. Pass the just-obtained values explicitly
      // (not via selectedBaseUrl/selectedApiKey) since this closure's
      // `formData` won't reflect the setFormData call above until the
      // next render.
      void handleFetchModels(result.base_url ? normalizeInstallerBaseUrl(result.base_url) : baseUrl, result.api_key)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.error('Keycloak login error:', message)
      setKeycloakError(message)
    } finally {
      setIsKeycloakLoading(false)
    }
  }

  const handleApplyExistingConfig = (config: ExistingConfig) => {
    const newBaseUrl = config.base_url ? normalizeInstallerBaseUrl(config.base_url) : formData.baseUrl
    const newApiKey = config.api_key ? config.api_key.trim() : formData.apiKey
    const newModel = config.model ? config.model.trim() : formData.modelName

    setFormData((prev) => ({
      ...prev,
      baseUrl: newBaseUrl,
      apiKey: newApiKey,
      modelName: newModel,
    }))
    setDismissedExisting(true)

    if (newBaseUrl) {
      void checkHealth(newBaseUrl)
    }

    if (newBaseUrl && newApiKey) {
      void handleFetchModels(newBaseUrl, newApiKey)
    }
  }

  return (
    <div className="hermes-fade-in flex h-full flex-col overflow-auto bg-background px-8 py-10">
      <div className="mx-auto w-full max-w-xl">
        <h1 className="mb-2 text-2xl font-semibold text-foreground">
          Konfiguration
        </h1>
        <p className="mb-6 text-sm text-muted-foreground">
          Richten Sie Ihren KI-Zugang und das gewünschte Sprachmodell ein.
        </p>

        {/* Existing Config Detection Banner */}
        {existingConfig && (existingConfig.api_key || existingConfig.base_url) && !dismissedExisting && (
          <div className="mb-6 rounded-lg border border-primary/30 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">
                  Vorhandene Konfiguration gefunden
                </p>
                <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {existingConfig.base_url && (
                    <p>Base URL: <code className="font-mono text-foreground">{existingConfig.base_url}</code></p>
                  )}
                  {existingConfig.api_key && (
                    <p>API-Key: <code className="font-mono text-foreground">••••••••{existingConfig.api_key.slice(-4)}</code></p>
                  )}
                  {existingConfig.model && (
                    <p>Modell: <code className="font-mono text-foreground">{existingConfig.model}</code></p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleApplyExistingConfig(existingConfig)}
                  className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  Übernehmen
                </button>
                <button
                  type="button"
                  onClick={() => setDismissedExisting(true)}
                  className="rounded p-1 text-muted-foreground hover:text-foreground transition-colors"
                  title="Verwerfen"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Base URL — hidden when baked in at packaging time */}
          {DEFAULT_BASE_URL ? (
            <div>
              <p className="block text-sm font-medium text-foreground">Base URL</p>
              <p className="mt-1 text-sm text-muted-foreground break-all">{selectedBaseUrl || DEFAULT_BASE_URL}</p>
              <LiteLLMHealthBadge checking={isCheckingHealth} healthy={litellmHealth} onRecheck={() => checkHealth(selectedBaseUrl || DEFAULT_BASE_URL)} />
            </div>
          ) : (
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
              <LiteLLMHealthBadge checking={isCheckingHealth} healthy={litellmHealth} onRecheck={() => checkHealth(selectedBaseUrl)} />
              {errors.baseUrl && (
                <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                  <AlertCircle className="h-3 w-3" />
                  {errors.baseUrl}
                </p>
              )}
            </div>
          )}

          {/* Anmeldedaten & Sign-On */}
          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-3 block text-sm font-medium text-foreground">
              Anmeldung & Zugangsdaten
            </legend>

            <div className="space-y-4">
              {/* Sign-On Button */}
              <div>
                <button
                  type="button"
                  onClick={() => void handleKeycloakLogin()}
                  disabled={isKeycloakLoading || !formData.baseUrl.trim()}
                  className="flex w-full items-center justify-center gap-2 rounded border border-primary bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                >
                  {isKeycloakLoading ? (
                    <>
                      <Loader className="h-4 w-4 animate-spin" />
                      Warte auf Browser-Anmeldung…
                    </>
                  ) : keycloakConnected ? (
                    <>
                      <Check className="h-4 w-4 text-green-500" />
                      Angemeldet via IAMDS Sign-On
                    </>
                  ) : (
                    <>
                      <ShieldCheck className="h-4 w-4" />
                      Mit IAMDS-Konto anmelden (Sign-On)
                    </>
                  )}
                </button>

                {keycloakConnected && (
                  <p className="mt-2 text-xs text-green-600 dark:text-green-400">
                    Zugangstoken erfolgreich bezogen. Wählen Sie unten Ihr Modell aus.
                  </p>
                )}
                {keycloakError && (
                  <p className="mt-2 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {keycloakError}
                  </p>
                )}
              </div>

              {/* Divider */}
              <div className="relative flex items-center py-1">
                <div className="grow border-t border-border"></div>
                <span className="shrink mx-3 text-xs text-muted-foreground uppercase tracking-wider">
                  oder manuell mit API-Key
                </span>
                <div className="grow border-t border-border"></div>
              </div>

              {/* API Key Input */}
              <div>
                <label htmlFor="apiKey" className="block text-sm font-medium text-foreground">
                  API-Key <span className="text-red-500">*</span>
                </label>
                <input
                  id="apiKey"
                  type="password"
                  value={formData.apiKey}
                  onChange={(e) => handleChange('apiKey', e.target.value)}
                  placeholder={keycloakConnected ? '(durch Sign-On hinterlegt)' : 'sk-…'}
                  className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary font-mono"
                />
                {errors.apiKey && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-3 w-3" />
                    {errors.apiKey}
                  </p>
                )}
              </div>
            </div>
          </fieldset>

          {/* Modell & Endpunkt */}
          <fieldset className="rounded-lg border border-border bg-muted/20 p-4">
            <legend className="mb-4 block text-sm font-medium text-foreground">
              Modell-Auswahl
            </legend>

            <div className="space-y-4">
              <div>
                <label htmlFor="modelName" className="block text-sm font-medium text-foreground">
                  Modell <span className="text-red-500">*</span>
                </label>
                {modelsFetched ? (
                  <select
                    id="modelName"
                    value={formData.modelName}
                    onChange={(e) => handleChange('modelName', e.target.value)}
                    className="mt-1 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    {availableModels.map((model) => (
                      <option key={model} value={model} className="bg-background text-foreground py-1">
                        {model}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="modelName"
                    type="text"
                    value=""
                    placeholder="Klicken Sie zuerst auf 'Modelle abrufen'"
                    disabled
                    className="mt-1 w-full rounded border border-input bg-muted px-3 py-2 text-sm text-muted-foreground disabled:opacity-50 disabled:cursor-not-allowed"
                  />
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void handleFetchModels()}
                    disabled={isLoadingModels || !selectedBaseUrl || !selectedApiKey.trim()}
                    className="flex items-center gap-2 rounded border border-input bg-muted/50 px-3 py-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isLoadingModels ? (
                      <>
                        <Loader className="h-3 w-3 animate-spin" />
                        Lade Modelle...
                      </>
                    ) : modelsFetched ? (
                      <>
                        <Check className="h-3 w-3 text-green-500" />
                        {availableModels.length} Modelle verfügbar
                      </>
                    ) : (
                      'Modelle abrufen'
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
                    Klicken Sie auf 'Modelle abrufen', um die Liste der verfügbaren Modelle zu laden.
                  </p>
                )}
              </div>
            </div>
          </fieldset>

          <div className="flex items-center justify-between gap-3 pt-4 border-t border-border/50">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setReportDialogOpen(true)}
              className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5"
            >
              <LifeBuoy className="h-3.5 w-3.5" />
              Problem melden
            </Button>
            <Button
              type="submit"
              size="lg"
              className="min-w-32"
              disabled={!modelsFetched}
            >
              Hermes installieren
            </Button>
          </div>
        </form>

        <ReportIssueDialog
          open={reportDialogOpen}
          onOpenChange={setReportDialogOpen}
          defaultSummary="Setup / Konfiguration Hilfe"
          defaultCategory="installation_update"
          defaultSeverity="medium"
          installType="fresh_install"
          contextType="manual"
        />
      </div>
    </div>
  )
}
