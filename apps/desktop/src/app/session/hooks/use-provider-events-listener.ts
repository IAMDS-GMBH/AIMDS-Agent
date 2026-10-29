import { useCallback } from 'react'

import { useEventsChannel } from './use-events-channel'

export interface SuiteAuthNeedsReauthEvent {
  type: 'suite_auth_needs_reauth'
  provider: string
  label: string
  domain: string
  http_status: number | null
  timestamp: string
}

/**
 * Hook to listen for provider-health events from the backend (AIS-394).
 *
 * Connects to /api/events?channel=provider_events and notifies the callback
 * when a Suite environment's key is found to need re-authentication. Shares
 * the connect / re-subscribe logic of the sibling "cron_events" channel
 * (AIS-755, see useEventsChannel).
 */
export function useProviderEventsListener(
  onSuiteAuthNeedsReauth?: (event: SuiteAuthNeedsReauthEvent) => Promise<void> | void,
  profile?: string
) {
  const onEvent = useCallback(
    (raw: unknown) => {
      const data = raw as SuiteAuthNeedsReauthEvent | null

      if (data?.type === 'suite_auth_needs_reauth' && onSuiteAuthNeedsReauth) {
        console.info(`[provider-events] ${data.provider} needs re-authentication`)
        void onSuiteAuthNeedsReauth(data)
      }
    },
    [onSuiteAuthNeedsReauth]
  )

  useEventsChannel('provider_events', 'provider-events', onSuiteAuthNeedsReauth ? onEvent : undefined, profile)
}
