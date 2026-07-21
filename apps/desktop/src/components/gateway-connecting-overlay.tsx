import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'
import { $desktopBoot } from '@/store/boot'
import { $gatewayState } from '@/store/session'
import { useI18n } from '@/i18n'
import { getEnvVars, getHermesConfig } from '@/hermes'

/**
 * Check if URL belongs to IAMDS domain or its subdomains.
 * Matches: "iamds.com", "litellm.iamds.com", "api.iamds.com", etc.
 */
function isIamdsUrl(url: string): boolean {
  if (!url) return false
  const lower = url.toLowerCase()
  return lower.endsWith('iamds.com') || lower.includes('.iamds.com')
}

// Static, always-legible prefix; only TAIL ever scrambles. Splitting them at
// the render level means no timer logic (even a stale HMR one) can ever
// scramble "CONN".
const PREFIX = 'CONN'
const TAIL = 'ECTING'
// Even-weight mono ascii so cycling glyphs don't jump width (matches the
// nousnet-web download-button decode effect).
const SCRAMBLE_CHARS = '/\\|-_=+<>~:*'
const TICK_MS = 45
const MESSAGE_STEP_MS = 1100
const STARTUP_MIN_MS = MESSAGE_STEP_MS * 4
const TEAM_MESSAGES_DE = [
  'Martin beendet gerade das EVN-Meeting.',
  'Tobias behebt gerade EasyPart-Shops.',
  'Michael kümmert sich gerade um organisatorische Themen.',
  'Johannes macht gerade das Dev-Deployment.',
  'Martin konfiguriert Keycloak zum hundertsten Mal...',
  'Tobias optimiert Datenbankabfragen im Schlaf...',
  'Michael ordnet Jira-Tickets nach Wichtigkeit...',
  'Johannes debuggt den Prompt-Cache...',
  'Martin sucht den Fehler im OAuth-Handshake...',
  'Tobias erklärt dem Kunden den Unterschied zwischen Bug und Feature...',
  'Michael plant das nächste Strategie-Meeting...',
  'Johannes erklärt dem Agenten, wie man fehlerfreien Code schreibt...',
  'Martin setzt die Passwort-Richtlinien auf Alphanumerisch-Hieroglyphisch...',
  'Tobias führt ein unangekündigtes Backup im Live-System durch...',
  'Michael schiebt Tickets im Kanban-Board hin und her...',
  'Johannes überzeugt den KI-Agenten, dass YAML besser ist als JSON...',
  'Martin startet den Docker-Daemon neu (schon wieder)...',
  'Tobias optimiert ein 300-Zeilen-SQL-Statement mit reinem Willen...',
  'Michael übersetzt Entwickler-Slang in verständliche Kunden-Präsentationen...',
  'Johannes optimiert die System-Prompts für maximale Höflichkeit...',
  'Martin diskutiert über das ultimative Kubernetes-Setup...',
  'Tobias jagt einen Race Condition-Bug im Warenkorb...',
  'Michael sucht den Mute-Button in Teams...',
  'Johannes testet die Grenzen des Kontextfensters mit Shakespeares Gesamtwerk...'
] as const

const TEAM_MESSAGES_EN = [
  'Martin is wrapping up the EVN meeting.',
  'Tobias is fixing EasyPart shops.',
  'Michael is taking care of organizational topics.',
  'Johannes is running the dev deployment.',
  'Martin is configuring Keycloak for the hundredth time...',
  'Tobias is optimizing database queries in his sleep...',
  'Michael is organizing Jira tickets by priority...',
  'Johannes is debugging the prompt cache...',
  'Martin is searching for the bug in the OAuth handshake...',
  'Tobias is explaining the difference between a bug and a feature to the client...',
  'Michael is planning the next strategy meeting...',
  'Johannes is explaining to the agent how to write bug-free code...',
  'Martin is setting password policies to alphanumeric hieroglyphics...',
  'Tobias is running an unannounced backup on the production database...',
  'Michael is moving tickets around the Kanban board...',
  'Johannes is convincing the AI agent that YAML is better than JSON...',
  'Martin is restarting the Docker daemon (yet again)...',
  'Tobias is optimizing a 300-line SQL statement with pure willpower...',
  'Michael is translating developer slang into understandable client presentations...',
  'Johannes is optimizing system prompts for maximum politeness...',
  'Martin is discussing the ultimate Kubernetes setup...',
  'Tobias is chasing a race condition bug in the shopping cart...',
  'Michael is searching for the mute button in Teams...',
  'Johannes is testing context window limits with Shakespeare\'s complete works...'
] as const

const BUSINESS_MESSAGES_DE = [
  'Kompiliere Kaffee-Zufuhr...',
  'Berechne optimales KPI-Reporting...',
  'Richte künstliche Intelligenz auf Umsatzziele aus...',
  'Schreibe passiv-aggressive E-Mails an das Management...',
  'Synchronisiere Synergien...',
  'Formatiere Excel-Tabellen...',
  'Platziere Haftnotizen an virtuellen Whiteboards...',
  'Verteile Schuldzuweisungen im Git-Commit-Log...',
  'Konfiguriere den Obstkorb in der Küche...',
  'Bereite Buzzwords für die nächste Präsentation vor...',
  'Analysiere agile Blockaden...',
  'Simuliere Produktivität im Home-Office...',
  'Bringe Server zum Glühen...',
  'Erhöhe Work-Life-Balance um 0.5%...',
  'Sammle Überstunden für das Wochenende...'
] as const

const BUSINESS_MESSAGES_EN = [
  'Compiling coffee supply...',
  'Calculating optimal KPI reporting...',
  'Aligning artificial intelligence with revenue goals...',
  'Drafting passive-aggressive emails to management...',
  'Synchronizing synergies...',
  'Formatting Excel spreadsheets...',
  'Placing sticky notes on virtual whiteboards...',
  'Distributing blame in the git commit log...',
  'Configuring the kitchen fruit basket...',
  'Preparing buzzwords for the next presentation...',
  'Analyzing agile blockers...',
  'Simulating home-office productivity...',
  'Making the servers glow...',
  'Increasing work-life balance by 0.5%...',
  'Accumulating overtime for the weekend...'
] as const

// Exit choreography (ms): text fades down + out, hold, then the overlay fades.
const TEXT_OUT_MS = 360
const POST_TEXT_HOLD_MS = 300
const OVERLAY_OUT_MS = 520
// Preview-only: how long to "connect" for, and the pause before replaying.
const PREVIEW_CONNECT_MS = 2600
const PREVIEW_REPLAY_MS = 1100

type Phase = 'live' | 'text-out' | 'overlay-out' | 'gone'

// Dev affordance: a warm Cmd+R reconnects almost instantly, so the overlay
// only flashes. Load with `?connecting=1` to force a looping preview.
function forcedPreview(): boolean {
  if (!import.meta.env.DEV || typeof window === 'undefined') {
    return false
  }

  try {
    return new URLSearchParams(window.location.search).get('connecting') === '1'
  } catch {
    return false
  }
}

function scrambledTail(resolvedCount: number): string {
  return Array.from(TAIL, (ch, i) =>
    i < resolvedCount ? ch : SCRAMBLE_CHARS[(Math.random() * SCRAMBLE_CHARS.length) | 0]
  ).join('')
}

export function GatewayConnectingOverlay() {
  const { locale } = useI18n()
  const gatewayState = useStore($gatewayState)
  const boot = useStore($desktopBoot)
  const [previewing] = useState(forcedPreview)
  const [tail, setTail] = useState(TAIL)
  const [phase, setPhase] = useState<Phase>('live')
  const shownAtRef = useRef<number | null>(null)
  const [isIamds, setIsIamds] = useState(false)

  // Dynamische Erkennung ob ein Endpunkt für iamds.com konfiguriert ist (best-effort)
  useEffect(() => {
    let active = true
    let attempt = 0
    const maxAttempts = 5

    async function checkEndpoint() {
      try {
        const desktop = window.hermesDesktop
        if (!desktop) return

        // 1. Electron Verbindungs-Konfiguration prüfen
        if (desktop.getConnectionConfig) {
          const config = await desktop.getConnectionConfig()
          if (config && config.mode === 'remote' && config.remoteUrl) {
            if (isIamdsUrl(config.remoteUrl)) {
              if (active) {
                setIsIamds(true)
                return
              }
            }
          }
        }

        // 2. Best-effort lokale Backend-Konfiguration prüfen
        const config = await getHermesConfig().catch(() => null)
        if (config && active) {
          const rawConfig = config as Record<string, any>
          const modelBaseUrl = rawConfig.model?.base_url || ''
          const litellmBaseUrl = rawConfig.litellm_hub?.base_url || ''
          if (isIamdsUrl(modelBaseUrl) || isIamdsUrl(litellmBaseUrl)) {
            setIsIamds(true)
            return
          }
        }

        // 3. Best-effort Umgebungsvariablen prüfen
        const envVars = await getEnvVars().catch(() => null)
        if (envVars && active) {
          for (const key of Object.keys(envVars)) {
            const val = envVars[key]
            if (key.toLowerCase().includes('iamds') && val?.is_set) {
              setIsIamds(true)
              return
            }
          }
        }

        // Wenn nicht gefunden, in 500ms nochmals probieren (falls Backend noch hochfährt)
        if (attempt < maxAttempts && !isIamds) {
          attempt++
          setTimeout(() => {
            if (active) checkEndpoint()
          }, 500)
        }
      } catch (err) {
        if (attempt < maxAttempts && !isIamds) {
          attempt++
          setTimeout(() => {
            if (active) checkEndpoint()
          }, 500)
        }
      }
    }

    checkEndpoint()

    return () => {
      active = false
    }
  }, [])

  const connecting = gatewayState !== 'open' && !boot.error
  const startupConnect = !previewing && (boot.running || boot.phase !== 'renderer.ready')
  // Latches once we've actually shown the overlay, so the brief frame where
  // gatewayState flips to "open" (connecting -> false) before the exit phase
  // kicks in doesn't unmount us and cause a flash.
  const shownRef = useRef(false)

  if (previewing || connecting) {
    shownRef.current = true
    if (!shownAtRef.current) {
      shownAtRef.current = Date.now()
    }
  }

  // Decode loop — only while live (freeze the resolved word during the exit).
  useEffect(() => {
    if (phase !== 'live' || (!previewing && !connecting)) {
      return
    }

    let resolved = 0
    let hold = 0

    const id = window.setInterval(() => {
      if (resolved >= TAIL.length) {
        hold += 1

        if (hold > 16) {
          resolved = 0
          hold = 0
        }

        setTail(TAIL)

        return
      }

      resolved += 0.5
      setTail(scrambledTail(Math.floor(resolved)))
    }, TICK_MS)

    return () => window.clearInterval(id)
  }, [phase, previewing, connecting])

  // Kick off the exit when connected: real connect, or a faked timer in preview.
  useEffect(() => {
    if (phase !== 'live') {
      return
    }

    if (previewing) {
      const id = window.setTimeout(() => {
        setTail(TAIL)
        setPhase('text-out')
      }, PREVIEW_CONNECT_MS)

      return () => window.clearTimeout(id)
    }

    if (gatewayState === 'open' && shownRef.current) {
      const shownAt = shownAtRef.current || Date.now()
      const elapsed = Date.now() - shownAt
      const waitMs = startupConnect ? Math.max(0, STARTUP_MIN_MS - elapsed) : 0
      const id = window.setTimeout(() => {
        setTail(TAIL)
        setPhase('text-out')
      }, waitMs)

      return () => window.clearTimeout(id)
    }
  }, [phase, previewing, gatewayState, startupConnect])

  // Advance the exit choreography: text-out -> overlay-out -> gone.
  useEffect(() => {
    if (phase === 'text-out') {
      const id = window.setTimeout(() => setPhase('overlay-out'), TEXT_OUT_MS + POST_TEXT_HOLD_MS)

      return () => window.clearTimeout(id)
    }

    if (phase === 'overlay-out') {
      const id = window.setTimeout(() => setPhase('gone'), OVERLAY_OUT_MS)

      return () => window.clearTimeout(id)
    }

    // Preview replays so we can keep watching the transition.
    if (phase === 'gone' && previewing) {
      const id = window.setTimeout(() => {
        setTail(TAIL)
        setPhase('live')
      }, PREVIEW_REPLAY_MS)

      return () => window.clearTimeout(id)
    }
  }, [phase, previewing])

  // Boot failed — BootFailureOverlay owns the screen; don't linger behind it.
  if (boot.error && !previewing) {
    return null
  }

  // Real connect: once the fade finishes, get out of the way for good.
  if (phase === 'gone' && !previewing) {
    return null
  }

  // Never showed (e.g. gateway already up on a warm reload) — stay out.
  if (!previewing && !connecting && !shownRef.current) {
    return null
  }

  const leaving = phase !== 'live'
  const overlayHidden = phase === 'overlay-out' || phase === 'gone'
  const shownElapsed = Math.max(0, Date.now() - (shownAtRef.current || Date.now()))

  // Bestimme die zu verwendende Nachrichtenliste basierend auf Sprache und iamds.com Endpunkt-Präsenz
  const messages = isIamds
    ? (locale === 'en' ? TEAM_MESSAGES_EN : TEAM_MESSAGES_DE)
    : (locale === 'en' ? BUSINESS_MESSAGES_EN : BUSINESS_MESSAGES_DE)

  // Modulo-basiertes Cycling, damit die Meldungen sich wiederholen, falls es länger dauert
  const messageIndex = Math.floor(shownElapsed / MESSAGE_STEP_MS) % messages.length
  const progressPct = Math.min(100, Math.round((shownElapsed / STARTUP_MIN_MS) * 100))
  const currentMessage = messages[messageIndex]

  return (
    <div
      className={cn(
        'fixed inset-0 z-[1200] grid place-items-center bg-(--ui-chat-surface-background) transition-opacity duration-500 ease-out',
        overlayHidden ? 'pointer-events-none opacity-0' : 'opacity-100'
      )}
    >
      <style>{'@keyframes gco-cursor { 0%, 49% { opacity: 1 } 50%, 100% { opacity: 0 } }'}</style>
      <div className="w-full max-w-xl px-6">
        <div
          className={cn(
            'inline-flex items-center pl-[0.4em] font-mono text-[0.95rem] font-semibold uppercase tracking-[0.44em] tabular-nums text-(--theme-primary) transition duration-300 ease-out',
            leaving ? 'translate-y-2 opacity-0 saturate-0' : 'translate-y-0 opacity-100 saturate-100'
          )}
        >
          <span>
            {PREFIX}
            {tail}
          </span>
          <span
            aria-hidden="true"
            className="dither ml-0.5 inline-block size-2 shrink-0 -translate-y-px rounded-[1px]"
            style={{ animation: 'gco-cursor 1s step-end infinite' }}
          />
        </div>
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-(--ui-stroke-secondary)">
          <div className="h-full bg-(--theme-primary) transition-all duration-300 ease-out" style={{ width: `${progressPct}%` }} />
        </div>
        <p className="mt-2 text-xs text-(--ui-text-secondary)">{currentMessage}</p>
      </div>
    </div>
  )
}
