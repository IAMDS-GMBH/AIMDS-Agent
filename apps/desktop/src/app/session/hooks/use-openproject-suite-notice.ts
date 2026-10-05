import { useEffect } from 'react'

import { translateNow } from '@/i18n'
import { notify } from '@/store/notifications'

// AIS-483: once the Suite took over a local OpenProject server, the backend
// uninstalls it and leaves a notice; tell the user once per notice.
const SEEN_KEY = 'hermes.desktop.openProjectSuiteNotice.v1'
const POLL_MS = 10 * 60_000

interface SuiteNotice {
  id: string
  instance?: string
  kind: string
  login?: string
}

function seen(id: string): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === id
  } catch {
    return false
  }
}

function markSeen(id: string) {
  try {
    window.localStorage.setItem(SEEN_KEY, id)
  } catch {
    // Best effort: the notice may show once more.
  }
}

export async function checkOpenProjectSuiteNotice(): Promise<boolean> {
  const desktop = window.hermesDesktop

  if (!desktop?.api) {
    return false
  }

  const status = await desktop
    .api<{ notice?: SuiteNotice | null }>({ path: '/api/openproject/suite-status' })
    .catch(() => null)

  const notice = status?.notice

  if (!notice?.id || notice.kind !== 'local_replaced' || seen(notice.id)) {
    return false
  }

  const title = translateNow('settings.mcp.openProjectReplacedTitle')
  const body = translateNow('settings.mcp.openProjectReplacedBody', notice.instance || '', notice.login || '')

  markSeen(notice.id)
  notify({ durationMs: 15_000, kind: 'info', message: body, title })
  void desktop.notify?.({ body, title })

  return true
}

export function useOpenProjectSuiteNotice(ready: boolean) {
  useEffect(() => {
    if (!ready) {
      return
    }

    void checkOpenProjectSuiteNotice()
    const timer = setInterval(() => void checkOpenProjectSuiteNotice(), POLL_MS)

    return () => clearInterval(timer)
  }, [ready])
}
