import { type MutableRefObject, useCallback, useState } from 'react'

import { getHermesConfig, getHermesConfigDefaults } from '@/hermes'
import { BUILTIN_PERSONALITIES, normalizePersonalityValue, personalityNamesFromConfig } from '@/lib/chat-runtime'
import {
  $currentCwd,
  setAvailablePersonalities,
  setCurrentCwd,
  setCurrentFastMode,
  setCurrentPersonality,
  setCurrentReasoningEffort,
  setCurrentServiceTier,
  setIntroPersonality
} from '@/store/session'
import { $notifications, dismissNotification, notify } from '@/store/notifications'

const DEFAULT_VOICE_SECONDS = 120
const FAST_TIERS = new Set(['fast', 'priority', 'on'])
// Fixed id so repeated refreshHermesConfig() calls (fired on nearly every
// state change) replace this notification in place instead of stacking
// duplicates, and so a later successful parse can dismiss it by id.
const CONFIG_PARSE_ERROR_NOTIFICATION_ID = 'config-parse-error'

function recordingLimit(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : DEFAULT_VOICE_SECONDS
}

interface HermesConfigOptions {
  activeSessionIdRef: MutableRefObject<string | null>
  refreshProjectBranch: (cwd: string) => Promise<void>
}

export function useHermesConfig({ activeSessionIdRef, refreshProjectBranch }: HermesConfigOptions) {
  const [voiceMaxRecordingSeconds, setVoiceMaxRecordingSeconds] = useState(DEFAULT_VOICE_SECONDS)
  const [sttEnabled, setSttEnabled] = useState(true)

  const refreshHermesConfig = useCallback(async () => {
    try {
      const [config, defaults] = await Promise.all([getHermesConfig(), getHermesConfigDefaults().catch(() => ({}))])

      const personality = normalizePersonalityValue(
        typeof config.display?.personality === 'string' ? config.display.personality : ''
      )

      setIntroPersonality(personality)
      // Active sessions keep their per-session value; standalone falls back to config.
      setCurrentPersonality(prev => (activeSessionIdRef.current ? prev || personality : personality))
      setAvailablePersonalities([
        ...new Set([
          'none',
          ...BUILTIN_PERSONALITIES,
          ...personalityNamesFromConfig(defaults),
          ...personalityNamesFromConfig(config)
        ])
      ])

      const cwd = (config.terminal?.cwd ?? '').trim()

      if (cwd && cwd !== '.') {
        setCurrentCwd(prev => prev || cwd)
        void refreshProjectBranch($currentCwd.get() || cwd)
      }

      const reasoning = (config.agent?.reasoning_effort ?? '').trim()
      const tier = (config.agent?.service_tier ?? '').trim()

      setCurrentReasoningEffort(prev => (activeSessionIdRef.current ? prev : reasoning))
      setCurrentServiceTier(prev => (activeSessionIdRef.current ? prev : tier))
      setCurrentFastMode(prev => (activeSessionIdRef.current ? prev : FAST_TIERS.has(tier.toLowerCase())))

      setVoiceMaxRecordingSeconds(recordingLimit(config.voice?.max_recording_seconds))
      setSttEnabled(config.stt?.enabled !== false)

      // config.yaml failed to parse and the backend fell back to defaults
      // (see hermes_cli/config.py::_warn_config_parse_failure) -- previously
      // only visible in ~/.hermes/logs, which normal end users never open.
      // Every user override (providers, fallback chain, model settings) is
      // silently being ignored, so surface it persistently until fixed.
      if (config.config_parse_error) {
        const { message, backup_path: backupPath } = config.config_parse_error
        notify({
          id: CONFIG_PARSE_ERROR_NOTIFICATION_ID,
          kind: 'error',
          title: 'config.yaml has an error',
          message,
          detail: backupPath ? `A copy of the broken file was saved to ${backupPath}.` : undefined,
          durationMs: 0
        })
      } else if ($notifications.get().some(n => n.id === CONFIG_PARSE_ERROR_NOTIFICATION_ID)) {
        // Only touch the store when the notification actually exists --
        // refreshHermesConfig runs on nearly every state change, and an
        // unconditional dismissNotification() would replace $notifications
        // with a new (content-identical) array reference every time.
        dismissNotification(CONFIG_PARSE_ERROR_NOTIFICATION_ID)
      }
    } catch {
      // Config is nice-to-have; chat still works without it.
    }
  }, [activeSessionIdRef, refreshProjectBranch])

  return { refreshHermesConfig, sttEnabled, voiceMaxRecordingSeconds }
}
