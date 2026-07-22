import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  getHermesConfigRecord,
  getMcpCatalog,
  type HermesGateway,
  installMcpCatalogEntry,
  saveHermesConfig
} from '@/hermes'
import { useI18n } from '@/i18n'
import { Wrench } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $activeSessionId } from '@/store/session'
import type { HermesConfigRecord, McpCatalogEntry } from '@/types/hermes'

import { EmptyState, LoadingState, Pill, SettingsContent } from './primitives'
import { useDeepLinkHighlight } from './use-deep-link-highlight'

interface McpSettingsProps {
  gateway?: HermesGateway | null
  onConfigSaved?: () => void
}

type McpServers = Record<string, Record<string, unknown>>

const EMPTY_SERVER = {
  command: '',
  args: [],
  env: {}
}

function getServers(config: HermesConfigRecord | null): McpServers {
  const raw = config?.mcp_servers

  return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as McpServers) : {}
}

const transportLabel = (server: Record<string, unknown>) =>
  typeof server.transport === 'string'
    ? server.transport
    : typeof server.url === 'string'
      ? 'http'
      : typeof server.command === 'string'
        ? 'stdio'
        : 'custom'

export function McpSettings({ gateway, onConfigSaved }: McpSettingsProps) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const activeSessionId = useStore($activeSessionId)
  const [config, setConfig] = useState<HermesConfigRecord | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)

  const [catalogEntries, setCatalogEntries] = useState<McpCatalogEntry[]>([])
  const [installModalEntry, setInstallModalEntry] = useState<McpCatalogEntry | null>(null)
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})
  const [installing, setInstalling] = useState(false)

  useEffect(() => {
    let cancelled = false

    getHermesConfigRecord()
      .then(next => {
        if (cancelled) {
          return
        }

        setConfig(next)
        const first = Object.keys(getServers(next)).sort()[0] ?? null
        setSelected(first)
      })
      .catch(err => notifyError(err, m.failedLoad))

    getMcpCatalog()
      .then(res => {
        if (!cancelled && res.entries) {
          setCatalogEntries(res.entries)
        }
      })
      .catch(() => {
        // Optional catalog fetch, ignore silent error
      })

    return () => void (cancelled = true)
  }, [])

  const servers = useMemo(() => getServers(config), [config])
  const names = useMemo(() => Object.keys(servers).sort(), [servers])

  useDeepLinkHighlight({
    block: 'nearest',
    elementId: serverName => `mcp-server-${serverName}`,
    onResolve: setSelected,
    param: 'server',
    ready: serverName => Boolean(config) && serverName in servers
  })

  useEffect(() => {
    const server = selected ? servers[selected] : null

    setName(selected ?? '')
    setBody(JSON.stringify(server ?? EMPTY_SERVER, null, 2))
  }, [selected, servers])

  if (!config) {
    return <LoadingState label={m.loading} />
  }

  const openInstallModal = (entry: McpCatalogEntry) => {
    if (entry.disabled) return
    const initialSecrets: Record<string, string> = {}
    const envVars = entry.required_env ?? entry.auth?.env ?? []

    for (const item of envVars) {
      if (item.default) {
        initialSecrets[item.name] = item.default
      }
    }
    setSecretInputs(initialSecrets)
    setInstallModalEntry(entry)
  }

  const handleInstallCatalog = async () => {
    if (!installModalEntry) {
      return
    }

    setInstalling(true)

    try {
      const result = await installMcpCatalogEntry({
        enable: true,
        name: installModalEntry.name,
        secrets: secretInputs
      })

      if (result.ok) {
        notify({
          kind: 'success',
          message: m.catalogInstallSuccessMessage(installModalEntry.name),
          title: m.catalogInstallSuccessTitle
        })
        const nextConfig = await getHermesConfigRecord()

        setConfig(nextConfig)
        setSelected(installModalEntry.name)
        onConfigSaved?.()
        setInstallModalEntry(null)
      } else {
        notify({ kind: 'error', message: result.message ?? m.saveFailed, title: m.saveFailed })
      }
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setInstalling(false)
    }
  }

  const saveServer = async () => {
    const nextName = name.trim()

    if (!nextName) {
      notify({ kind: 'error', message: m.nameRequiredMessage, title: m.nameRequiredTitle })

      return
    }

    let parsed: Record<string, unknown>

    try {
      const raw = JSON.parse(body)

      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error(m.objectRequired)
      }

      parsed = raw as Record<string, unknown>
    } catch (err) {
      notifyError(err, m.invalidJson)

      return
    }

    setSaving(true)

    try {
      const nextServers = { ...servers }

      if (selected && selected !== nextName) {
        delete nextServers[selected]
      }

      nextServers[nextName] = parsed

      const nextConfig = { ...config, mcp_servers: nextServers }

      await saveHermesConfig(nextConfig)
      setConfig(nextConfig)
      setSelected(nextName)
      onConfigSaved?.()
      notify({ kind: 'success', message: m.savedMessage(nextName), title: m.savedTitle })
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  const removeServer = async (serverName: string) => {
    setSaving(true)

    try {
      const nextServers = { ...servers }

      delete nextServers[serverName]

      const nextConfig = { ...config, mcp_servers: nextServers }

      await saveHermesConfig(nextConfig)
      setConfig(nextConfig)
      setSelected(Object.keys(nextServers).sort()[0] ?? null)
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, m.removeFailed)
    } finally {
      setSaving(false)
    }
  }

  const reloadMcp = async () => {
    if (!gateway) {
      notify({ kind: 'warning', message: m.gatewayUnavailableMessage, title: m.gatewayUnavailableTitle })

      return
    }

    setReloading(true)

    try {
      const result = await gateway.request<{
        ok?: boolean
        message?: string
        summary?: {
          connected?: number
          configured_enabled?: number
          failed?: number
          failed_servers?: string[]
        }
      }>('reload.mcp', {
        confirm: true,
        session_id: activeSessionId ?? undefined
      })

      if (result?.ok === false) {
        const failedNames = result.summary?.failed_servers?.join(', ')

        notify({
          kind: 'warning',
          message: failedNames
            ? `${result.message ?? m.reloadedMessage} (${failedNames})`
            : (result.message ?? m.reloadedMessage),
          title: m.reloadedTitle
        })
      } else {
        notify({
          kind: 'success',
          message: result?.message ?? m.reloadedMessage,
          title: m.reloadedTitle
        })
      }
    } catch (err) {
      notifyError(err, m.reloadFailed)
    } finally {
      setReloading(false)
    }
  }

  return (
    <SettingsContent>
      {catalogEntries.length > 0 && (
        <div className="mb-8 grid gap-3 border-b border-(--stroke-nous) pb-6">
          <div>
            <h3 className="text-sm font-semibold">{m.catalogSectionTitle}</h3>
            <p className="text-xs text-muted-foreground">{m.catalogSectionDesc}</p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {catalogEntries.map(entry => {
              const isInstalled = Boolean(servers[entry.name])
              const isDisabled = entry.disabled === true

              return (
                <div
                  className={cn(
                    "flex flex-col justify-between rounded-lg border border-(--stroke-nous) bg-(--ui-bg-tertiary) p-3",
                    isDisabled ? "opacity-60" : ""
                  )}
                  key={entry.name}
                >
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-sm capitalize">{entry.name}</span>
                      <Pill>{isDisabled ? 'In Entwicklung' : (entry.source ? entry.source.split(' ')[0] : 'catalog')}</Pill>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{entry.description}</p>
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-(--stroke-nous) pt-2">
                    <span className="text-xs text-muted-foreground">{isInstalled ? m.catalogInstalled : ''}</span>
                    <Button
                      disabled={isDisabled}
                      onClick={() => openInstallModal(entry)}
                      size="xs"
                      variant={isDisabled ? 'ghost' : isInstalled ? 'secondary' : 'default'}
                    >
                      {isDisabled ? 'In Entwicklung' : isInstalled ? m.editServer : m.catalogInstall}
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="mb-4 flex items-center justify-between gap-4">
        <h3 className="text-sm font-semibold">{m.customServersTitle}</h3>
        <div className="flex items-center gap-4">
          <Button onClick={() => setSelected(null)} size="xs" variant="text">
            {m.newServer}
          </Button>
          <Button disabled={reloading} onClick={() => void reloadMcp()} size="xs" variant="text">
            {reloading ? m.reloading : m.reload}
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <div className="min-h-64">
          {names.length === 0 ? (
            <EmptyState description={m.emptyDesc} title={m.emptyTitle} />
          ) : (
            <div className="grid gap-0.5">
              {names.map(serverName => {
                const server = servers[serverName]
                const active = selected === serverName

                return (
                  <button
                    className={cn(
                      'scroll-mt-2 rounded-md px-2 py-2 text-left transition-colors hover:bg-(--chrome-action-hover)',
                      active ? 'bg-(--ui-bg-tertiary) text-foreground' : 'text-muted-foreground'
                    )}
                    id={`mcp-server-${serverName}`}
                    key={serverName}
                    onClick={() => setSelected(serverName)}
                    type="button"
                  >
                    <div className="truncate text-sm font-medium">{serverName}</div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <Pill>{transportLabel(server)}</Pill>
                      {server.disabled === true && <Pill>{m.disabled}</Pill>}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="grid content-start gap-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Wrench className="size-4 text-muted-foreground" />
            {selected ? m.editServer : m.newServer}
          </div>
          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">{m.name}</span>
            <Input onChange={event => setName(event.currentTarget.value)} placeholder="filesystem" value={name} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">{m.serverJson}</span>
            <Textarea
              className="min-h-80 font-mono text-xs"
              onChange={event => setBody(event.currentTarget.value)}
              spellCheck={false}
              value={body}
            />
          </label>
          <div className="flex items-center justify-between">
            {selected ? (
              <Button
                className="text-destructive hover:text-destructive"
                disabled={saving}
                onClick={() => void removeServer(selected)}
                size="xs"
                variant="text"
              >
                {m.remove}
              </Button>
            ) : (
              <span />
            )}
            <Button disabled={saving} onClick={() => void saveServer()} size="sm">
              {saving ? t.common.saving : m.saveServer}
            </Button>
          </div>
        </div>
      </div>

      <Dialog onOpenChange={open => !open && setInstallModalEntry(null)} open={Boolean(installModalEntry)}>
        {installModalEntry && (
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>{m.catalogModalTitle(installModalEntry.name)}</DialogTitle>
              <DialogDescription>{m.catalogModalDesc}</DialogDescription>
            </DialogHeader>

            <div className="grid gap-3 py-2">
              {(installModalEntry.required_env ?? installModalEntry.auth?.env ?? []).map(item => (
                <label className="grid gap-1" key={item.name}>
                  <span className="font-medium text-xs">
                    {item.prompt || item.name}
                    {item.required && <span className="ml-0.5 text-destructive">*</span>}
                  </span>
                  <Input
                    onChange={e =>
                      setSecretInputs(prev => ({
                        ...prev,
                        [item.name]: e.target.value
                      }))
                    }
                    placeholder={item.default || item.name}
                    type={item.secret ? 'password' : 'text'}
                    value={secretInputs[item.name] ?? ''}
                  />
                </label>
              ))}
              <p className="text-[11px] text-muted-foreground">{m.catalogSecretsNotice}</p>
            </div>

            <DialogFooter>
              <Button onClick={() => setInstallModalEntry(null)} size="xs" variant="ghost">
                {t.common.cancel}
              </Button>
              <Button disabled={installing} onClick={() => void handleInstallCatalog()} size="xs">
                {installing ? m.catalogInstalling : m.catalogInstall}
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </SettingsContent>
  )
}
