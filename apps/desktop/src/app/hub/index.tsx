import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Codicon } from '@/components/ui/codicon'
import { TextTab } from '@/components/ui/text-tab'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'

import { useGatewayRequest } from '../gateway/hooks/use-gateway-request'
import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { PAGE_INSET_X } from '../layout-constants'
import { PageSearchShell } from '../page-search-shell'
import { includesQuery } from '../settings/helpers'

const HUB_MODES = ['agents', 'skills'] as const
type HubMode = (typeof HUB_MODES)[number]

interface LiteLLMAgent {
  id: string
  name: string
  description?: string
  active: boolean
}

interface LiteLLMSkill {
  id: string
  name: string
  description?: string
  source?: string
  sourceRaw?: unknown
  installed?: boolean
  installedName?: string
}

interface InstallProgressState {
  message: string
  percent: number | null
  level?: 'info' | 'success' | 'warning' | 'error'
}

function resolveSkillSource(source: unknown): string | undefined {
  if (!source) {
    return undefined
  }

  if (typeof source === 'string') {
    const trimmed = source.trim()

    return trimmed || undefined
  }

  if (typeof source !== 'object') {
    return undefined
  }

  const srcObj = source as Record<string, unknown>

  const repo =
    (typeof srcObj.repo === 'string' && srcObj.repo.trim()) ||
    (typeof srcObj.repository === 'string' && srcObj.repository.trim()) ||
    undefined

  if (repo) {
    return `github:${repo}`
  }

  const owner =
    (typeof srcObj.owner === 'string' && srcObj.owner.trim()) ||
    (typeof srcObj.org === 'string' && srcObj.org.trim()) ||
    undefined

  const repoName =
    (typeof srcObj.name === 'string' && srcObj.name.trim()) ||
    (typeof srcObj.repo_name === 'string' && srcObj.repo_name.trim()) ||
    undefined

  const provider =
    (typeof srcObj.source === 'string' && srcObj.source.trim().toLowerCase()) ||
    (typeof srcObj.provider === 'string' && srcObj.provider.trim().toLowerCase()) ||
    undefined

  if ((provider === 'github' || provider === 'git') && owner && repoName) {
    return `github:${owner}/${repoName}`
  }

  const url =
    (typeof srcObj.url === 'string' && srcObj.url.trim()) ||
    (typeof srcObj.html_url === 'string' && srcObj.html_url.trim()) ||
    (typeof srcObj.raw_url === 'string' && srcObj.raw_url.trim()) ||
    (typeof srcObj.source_url === 'string' && srcObj.source_url.trim()) ||
    undefined

  return url || undefined
}

function filteredAgents(agents: LiteLLMAgent[], query: string): LiteLLMAgent[] {
  const q = query.trim().toLowerCase()

  return agents
    .filter(agent => {
      if (!q) {
        return true
      }

      return (
        includesQuery(agent.name, q) ||
        includesQuery(agent.description || '', q)
      )
    })
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
}

function filteredSkills(skills: LiteLLMSkill[], query: string): LiteLLMSkill[] {
  const q = query.trim().toLowerCase()

  return skills
    .filter(skill => {
      if (!q) {
        return true
      }

      return (
        includesQuery(skill.name, q) ||
        includesQuery(skill.description || '', q)
      )
    })
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
}

interface HubViewProps extends React.ComponentProps<'section'> {}

export function HubView({ ...props }: HubViewProps) {
  const { t } = useI18n()
  const { requestGateway } = useGatewayRequest()
  const gateway = useStore($gateway)
  const [mode, setMode] = useRouteEnumParam('tab', HUB_MODES, 'agents')

  const [query, setQuery] = useState('')
  const [agents, setAgents] = useState<LiteLLMAgent[] | null>(null)
  const [skills, setSkills] = useState<LiteLLMSkill[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resolvedUrl, setResolvedUrl] = useState<string | null>(null)
  const [rawResponse, setRawResponse] = useState<unknown>(null)
  const [showRaw, setShowRaw] = useState(false)
  const [installing, setInstalling] = useState<{ id: string; skill: LiteLLMSkill } | null>(null)

  const [installProgress, setInstallProgress] = useState<InstallProgressState>({
    message: '',
    percent: null,
    level: 'info',
  })

  const installRunIdRef = useRef<string | null>(null)
  const installProgressRef = useRef<InstallProgressState>(installProgress)
  const [installedSkillIds, setInstalledSkillIds] = useState<Set<string>>(new Set())
  const [togglingAgent, setTogglingAgent] = useState<string | null>(null)

  useEffect(() => {
    installProgressRef.current = installProgress
  }, [installProgress])

  useEffect(() => {
    if (!gateway) {
      return
    }

    return gateway.onEvent(event => {
      if (event.type !== 'litellm_hub.skill_install.progress') {
        return
      }

      const payload = (event.payload as Record<string, unknown>) || {}
      const runId = typeof payload.install_run_id === 'string' ? payload.install_run_id : ''

      if (!runId || installRunIdRef.current !== runId) {
        return
      }

      const total = Number(payload.total) || 0
      const completed = Number(payload.completed) || 0
      const failed = Number(payload.failed) || 0
      const conflicts = Number(payload.conflicts) || 0
      const hasFailures = failed > 0 || conflicts > 0

      const message =
        typeof payload.message === 'string' && payload.message.trim()
          ? payload.message
          : `Installing skills... (${Math.max(0, completed)}/${Math.max(0, total)})`

      let percent = total > 0 ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : 0

      if (hasFailures) {
        percent = Math.min(95, percent)
      }

      setInstallProgress(prev => ({
        ...prev,
        message,
        percent,
        level: hasFailures ? 'warning' : 'info',
      }))
    })
  }, [gateway])

  // Refresh handlers
  const refresh = async () => {
    setError(null)
    setResolvedUrl(null)
    setRawResponse(null)
    setShowRaw(false)

    try {
      if (mode === 'agents') {
        const data = await requestGateway<{ agents: unknown[]; resolved_url?: string }>('litellm_hub.agents', { limit: 100 })
        console.log('[Hub] agents resolved_url:', data?.resolved_url)
        setResolvedUrl(data?.resolved_url || null)
        setRawResponse(data)

        const agentsList = (data?.agents || []).map((agent: unknown) => {
          const a = agent as Record<string, unknown>

          return {
            id: String(a.id || a.name || ''),
            name: String(a.name || ''),
            description: a.description ? String(a.description) : undefined,
            active: Boolean(a.active),
          }
        })

        setAgents(agentsList)
      } else {
        const data = await requestGateway<{ skills: unknown[]; resolved_url?: string }>('litellm_hub.skills', { limit: 100 })
        console.log('[Hub] skills resolved_url:', data?.resolved_url)
        setResolvedUrl(data?.resolved_url || null)
        setRawResponse(data)

        const skillsList = (data?.skills || []).map((skill: unknown) => {
          const s = skill as Record<string, unknown>
          const sourceStr = resolveSkillSource(s.source)

          return {
            id: String(s.id || s.name || ''),
            name: String(s.name || ''),
            description: s.description ? String(s.description) : undefined,
            source: sourceStr,
            sourceRaw: s.source,
            installed: Boolean(s.installed),
            installedName: s.installed_name ? String(s.installed_name) : undefined,
          }
        })

        setSkills(skillsList)
        setInstalledSkillIds(new Set(skillsList.filter(s => s.installed).map(s => s.id)))
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      console.error('[Hub] load error:', message)
      setError(message)
      setRawResponse({ error: message })
      notifyError(err, `Failed to load ${mode}`)
    }
  }

  const handleToggleAgent = async (agent: LiteLLMAgent) => {
    setTogglingAgent(agent.name)

    try {
      const method = agent.active ? 'litellm_hub.agent_deactivate' : 'litellm_hub.agent_activate'
      await requestGateway<{ active_agents: string[] }>(method, { agent_name: agent.name })
      // Optimistically update local state
      setAgents(prev => prev
        ? prev.map(a => a.name === agent.name ? { ...a, active: !agent.active } : a)
        : prev
      )
    } catch (err) {
      notifyError(err, `Failed to ${agent.active ? 'deactivate' : 'activate'} ${agent.name}`)
    } finally {
      setTogglingAgent(null)
    }
  }

  const handleInstallSkill = async (skill: LiteLLMSkill) => {
    const installRunId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    installRunIdRef.current = installRunId

    const isSetInstall =
      typeof skill.sourceRaw === 'object' &&
      skill.sourceRaw !== null &&
      typeof (skill.sourceRaw as Record<string, unknown>).path === 'string'

    setInstalling({ id: skill.id, skill })
    setInstallProgress({
      message: isSetInstall ? 'Preparing skill set installation...' : 'Starting installation...',
      percent: 0,
      level: 'info',
    })

    try {
      if (!isSetInstall) {
        setInstallProgress({ message: 'Downloading skill from GitHub...', percent: 35, level: 'info' })
      }

      const sourceParam = skill.sourceRaw ?? skill.source

      const result = await requestGateway<{
        success: boolean
        message: string
        warning?: boolean
        set_name?: string
        installed_skills?: string[]
        skipped_skills?: string[]
        conflict_skills?: string[]
        failed_skills?: string[]
      }>('litellm_hub.skill_install', {
        skill_id: skill.id,
        skill_name: skill.name,
        source: sourceParam,
        install_run_id: installRunId,
      })

      if (result?.success) {
        const warning = Boolean(result.warning)
        const conflicts = Array.isArray(result.conflict_skills) ? result.conflict_skills.length : 0
        const failed = Array.isArray(result.failed_skills) ? result.failed_skills.length : 0
        const hasFailures = conflicts > 0 || failed > 0
        const finalPercent = hasFailures ? Math.min(95, installProgressRef.current.percent ?? 95) : 100

        if (Array.isArray(result.installed_skills) && result.installed_skills.length > 0) {
          const skipped = Array.isArray(result.skipped_skills) ? result.skipped_skills.length : 0
          const suffix = skipped > 0 ? ` (${skipped} already installed)` : ''
          const base = `${warning ? '⚠' : '✓'} Installed ${result.installed_skills.length} skills from ${result.set_name || skill.name}${suffix}`
          const warnSuffix = conflicts > 0 ? ` (${conflicts} conflict warning${conflicts > 1 ? 's' : ''})` : ''
          setInstallProgress({
            message: `${base}${warnSuffix}`,
            percent: finalPercent,
            level: warning ? 'warning' : 'success',
          })
        } else {
          setInstallProgress({
            message: `${warning ? '⚠' : '✓'} ${result.message || `Successfully installed: ${skill.name}`}`,
            percent: finalPercent,
            level: warning ? 'warning' : 'success',
          })
        }

        setInstalledSkillIds(prev => new Set(prev).add(skill.id))
        installRunIdRef.current = null
        setTimeout(() => setInstalling(null), 2000)
      } else {
        setInstallProgress({
          message: `✗ Installation failed: ${result?.message || 'Unknown error'}`,
          percent: Math.min(95, installProgressRef.current.percent ?? 95),
          level: 'error',
        })
        installRunIdRef.current = null
      }

    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)

      const friendlyMessage = /(?:^|\\b)(429|rate limit|too many requests|server busy)(?:\\b|$)/i.test(message)
        ? 'Server busy, try again later.'
        : message

      setInstallProgress({
        message: `✗ Error: ${friendlyMessage}`,
        percent: Math.min(95, installProgressRef.current.percent ?? 95),
        level: 'error'
      })
      installRunIdRef.current = null
      notifyError(err, `Failed to install ${skill.name}`)
    }
  }

  const handleUninstallSkill = async (skill: LiteLLMSkill) => {
    const uninstallName = (skill.installedName || skill.name || skill.id).trim()

    try {
      await requestGateway<{ success: boolean; message: string }>('litellm_hub.skill_uninstall', {
        skill_name: uninstallName,
        skill_id: skill.id,
      })
      setInstalledSkillIds(prev => {
        const next = new Set(prev)
        next.delete(skill.id)

        return next
      })
      setSkills(prev =>
        prev?.map(s =>
          s.id === skill.id
            ? { ...s, installed: false, installedName: undefined }
            : s
        ) || prev
      )
    } catch (err) {
      notifyError(err, `Failed to uninstall ${uninstallName}`)
    }
  }

  // Initial load
  useEffect(() => {
    void refresh()
  }, [mode])

  // Refresh hotkey (Cmd+R / Ctrl+R)
  useRefreshHotkey(refresh)

  // Compute filtered lists
  const filteredAgentsList = useMemo(() => {
    return agents ? filteredAgents(agents, query) : null
  }, [agents, query])

  const filteredSkillsList = useMemo(() => {
    return skills ? filteredSkills(skills, query) : null
  }, [skills, query])

  const isLoading = (mode === 'agents' && agents === null) || (mode === 'skills' && skills === null)

  return (
    <section {...props} className={cn('flex flex-col overflow-hidden', props.className)}>
      <PageSearchShell
        onSearchChange={setQuery}
        searchPlaceholder={
          mode === 'agents' ? 'Search agents...' : 'Search skills...'
        }
        searchValue={query}
        tabs={
          <>
            <TextTab
              active={mode === 'agents'}
              className="data-[active]:bg-accent/5"
              onClick={() => setMode('agents')}
            >
              <span>Agents</span>
              {filteredAgentsList && (
                <Badge className="ml-2 pointer-events-none" variant="outline">
                  {filteredAgentsList.length}
                </Badge>
              )}
            </TextTab>

            <TextTab
              active={mode === 'skills'}
              className="data-[active]:bg-accent/5"
              onClick={() => setMode('skills')}
            >
              <span>Skills</span>
              {filteredSkillsList && (
                <Badge className="ml-2 pointer-events-none" variant="outline">
                  {filteredSkillsList.length}
                </Badge>
              )}
            </TextTab>
          </>
        }
      >
        {/* Fetching from URL — hidden until further development
        {resolvedUrl && (
          <div className="px-4 py-2 text-xs text-muted-foreground bg-secondary/50 border-b border-border m-0 font-mono break-all">
            Fetching from: {resolvedUrl}
          </div>
        )}
        */}

        {error && (
          <div className="px-4 py-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 m-4 rounded">
            {error}
          </div>
        )}

        {isLoading ? (
          <PageLoader />
        ) : mode === 'agents' ? (
          <AgentsList
            agents={filteredAgentsList || []}
            onToggle={handleToggleAgent}
            query={query}
            togglingAgent={togglingAgent}
          />
        ) : (
        <SkillsList
          installedIds={installedSkillIds}
          onInstall={handleInstallSkill}
          onUninstall={handleUninstallSkill}
          query={query}
          skills={filteredSkillsList || []}
        />
        )}

        {/* Raw server response — hidden until further development
        {rawResponse !== null && (
          <div className="border-t border-border mt-2">
            <button
              onClick={() => setShowRaw(v => !v)}
              className="w-full flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-accent/5 transition-colors text-left"
            >
              <span className={cn('transition-transform', showRaw ? 'rotate-90' : '')}>▶</span>
              Raw server response
            </button>
            {showRaw && (
              <pre className="px-4 pb-4 text-xs font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                {JSON.stringify(rawResponse, null, 2)}
              </pre>
            )}
          </div>
        )}
        */}
        
        {installing && (
          <SkillInstallModal
            onClose={() => {
              installRunIdRef.current = null
              setInstalling(null)
            }}
            progress={installProgress}
            skill={installing.skill}
          />
        )}
      </PageSearchShell>
    </section>
  )
}

interface AgentsListProps {
  agents: LiteLLMAgent[]
  query: string
  togglingAgent: string | null
  onToggle: (agent: LiteLLMAgent) => void
}

function AgentsList({ agents, query, togglingAgent, onToggle }: AgentsListProps) {
  return (
    <div className="overflow-y-auto flex-1">
      {agents.length === 0 ? (
        <div className={cn('flex items-center justify-center h-full text-sm text-muted-foreground', PAGE_INSET_X)}>
          {query ? 'No agents match your search.' : 'No agents available.'}
        </div>
      ) : (
        <div className={cn('space-y-1 p-4', PAGE_INSET_X)}>
          {agents.map(agent => (
            <div
              className={cn(
                'p-3 rounded border transition-colors',
                agent.active
                  ? 'border-blue-500/40 bg-blue-500/5 dark:bg-blue-500/10'
                  : 'border-border bg-card hover:bg-accent/5'
              )}
              key={agent.id}
            >
              <div className="flex items-start gap-2">
                <Codicon className="mt-1 flex-shrink-0" name="robot" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-foreground">{agent.name}</span>
                    {agent.active && (
                      <Badge className="text-xs text-blue-600 dark:text-blue-400 border-blue-500/40" variant="outline">
                        active
                      </Badge>
                    )}
                  </div>
                  {agent.description && (
                    <div className="text-sm text-muted-foreground mt-1 line-clamp-2">
                      {agent.description}
                    </div>
                  )}
                </div>
                <button
                  className={cn(
                    'ml-2 px-2.5 py-1 text-xs font-medium rounded transition-colors flex-shrink-0',
                    agent.active
                      ? 'text-blue-600 dark:text-blue-400 border border-blue-500/40 hover:bg-red-50 dark:hover:bg-red-950/30 hover:text-red-600 dark:hover:text-red-400 hover:border-red-400'
                      : 'text-white bg-blue-600 hover:bg-blue-700',
                    togglingAgent === agent.name && 'opacity-50 cursor-not-allowed'
                  )}
                  disabled={togglingAgent === agent.name}
                  onClick={() => onToggle(agent)}
                >
                  {togglingAgent === agent.name
                    ? '...'
                    : agent.active
                      ? 'Deactivate'
                      : 'Activate'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface SkillsListProps {
  skills: LiteLLMSkill[]
  query: string
  onInstall?: (skill: LiteLLMSkill) => void
  onUninstall?: (skill: LiteLLMSkill) => void
  installedIds?: Set<string>
}

function SkillsList({ skills, query, onInstall, onUninstall, installedIds }: SkillsListProps) {
  return (
    <div className="overflow-y-auto flex-1">
      {skills.length === 0 ? (
        <div className={cn('flex items-center justify-center h-full text-sm text-muted-foreground', PAGE_INSET_X)}>
          {query ? 'No skills match your search.' : 'No skills available.'}
        </div>
      ) : (
        <div className={cn('space-y-1 p-4', PAGE_INSET_X)}>
          {skills.map(skill => (
            <div
              className="p-3 rounded border border-border bg-card hover:bg-accent/5 transition-colors"
              key={skill.id}
            >
              <div className="flex items-start gap-2">
                <Codicon className="mt-1" name="lightbulb" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-foreground">{skill.name}</div>
                  {skill.description && (
                    <div className="text-sm text-muted-foreground mt-1 line-clamp-2">
                      {skill.description}
                    </div>
                  )}
                  {skill.source && (
                    <div className="text-xs text-muted-foreground mt-2">
                      Source: {skill.source}
                    </div>
                  )}
                </div>
                {onInstall && (
                  <div className="ml-2 flex-shrink-0 flex flex-col items-end gap-1">
                    {installedIds?.has(skill.id) ? (
                      <>
                        <Badge className="text-xs text-green-600 dark:text-green-400 border-green-500/40" variant="outline">
                          ✓ Installed
                        </Badge>
                        {onUninstall && (
                          <button
                            className="px-2.5 py-1 text-xs font-medium rounded transition-colors border border-red-400/50 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30"
                            onClick={() => onUninstall(skill)}
                          >
                            Uninstall
                          </button>
                        )}
                      </>
                    ) : (
                      <button
                        className="px-2.5 py-1 text-xs font-medium rounded transition-colors text-white bg-blue-600 hover:bg-blue-700"
                        onClick={() => onInstall(skill)}
                      >
                        Install
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface SkillInstallModalProps {
  skill: LiteLLMSkill
  progress: InstallProgressState
  onClose: () => void
}

function SkillInstallModal({ skill, progress, onClose }: SkillInstallModalProps) {
  const isDone = ['success', 'warning', 'error'].includes(progress.level || '')

  const isSetInstall =
    typeof skill.sourceRaw === 'object' &&
    skill.sourceRaw !== null &&
    typeof (skill.sourceRaw as Record<string, unknown>).path === 'string'

  const percent = progress.percent
  const level = progress.level || 'info'

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background border border-border rounded-lg shadow-lg w-full max-w-md mx-4 p-6">
        <h2 className="text-lg font-semibold mb-4">Installing {skill.name}</h2>
        <div className="space-y-3">
          <div className="h-2 rounded bg-muted/50 overflow-hidden">
            {typeof percent === 'number' ? (
              <div
                className={cn(
                  'h-full transition-all duration-300',
                  isDone
                    ? level === 'success'
                      ? 'bg-green-500'
                      : level === 'warning'
                        ? 'bg-amber-500'
                        : 'bg-red-500'
                    : 'bg-blue-500'
                )}
                style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
              />
            ) : (
              <div className="h-full w-1/3 bg-blue-500 animate-pulse" />
            )}
          </div>
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'w-4 h-4 rounded-full',
                isDone
                  ? level === 'success'
                    ? 'bg-green-500'
                    : level === 'warning'
                      ? 'bg-amber-500'
                      : 'bg-red-500'
                  : 'bg-blue-500 animate-pulse'
              )}
            ></div>
            <p className="text-sm">{progress.message}</p>
          </div>
          {isSetInstall && !isDone && (
            <p className="text-xs text-muted-foreground">
              Installing a skill set can take a bit longer because multiple skills are fetched.
            </p>
          )}
          {isDone && (
            <div className="pt-2">
              <button
                className="px-3 py-1.5 text-xs font-medium rounded border border-border hover:bg-accent/40 transition-colors"
                onClick={onClose}
              >
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
