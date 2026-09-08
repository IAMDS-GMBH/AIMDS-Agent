import { useEffect } from 'react'

import { $unseenCronOutputCount } from '@/store/cron'
import { isSecondaryWindow } from '@/store/windows'

// Mirror the unseen cron-output count onto the OS badge (dock on macOS/Linux,
// taskbar overlay on Windows). Only the primary window owns the badge — a
// secondary session window shares the process-wide badge and would fight it.
export function useCronUnreadBadge() {
  useEffect(() => {
    if (isSecondaryWindow()) {
      return
    }

    const setBadge = window.hermesDesktop?.setUnreadBadge

    if (typeof setBadge !== 'function') {
      return
    }

    let last = -1

    const unsubscribe = $unseenCronOutputCount.subscribe(count => {
      if (count === last) {
        return
      }

      last = count
      void setBadge(count).catch(() => undefined)
    })

    return () => {
      unsubscribe()
      void setBadge(0).catch(() => undefined)
    }
  }, [])
}
