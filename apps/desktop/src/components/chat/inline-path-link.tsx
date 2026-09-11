import { useState } from 'react'

import { translateNow } from '@/i18n'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { setCurrentSessionPreviewTarget } from '@/store/preview'
import { $currentCwd } from '@/store/session'

interface InlinePathLinkProps {
  // Render as inline code (the path came from a backtick span).
  code?: boolean
  path: string
}

// A bare file path in assistant prose, rendered as a clickable affordance that
// opens the file in the preview pane (file lane, 'manual' source — never
// persisted, never auto-opened). Errors surface as a toast.
export function InlinePathLink({ code = false, path }: InlinePathLinkProps) {
  const [opening, setOpening] = useState(false)

  async function open() {
    if (opening) {
      return
    }

    setOpening(true)

    try {
      const target = await normalizeOrLocalPreviewTarget(path, $currentCwd.get() || undefined)

      if (!target) {
        throw new Error(path)
      }

      setCurrentSessionPreviewTarget(target, 'manual', path)
    } catch (error) {
      notifyError(error, translateNow('artifacts.openFailed'))
    } finally {
      setOpening(false)
    }
  }

  return (
    <button
      className={cn(
        'inline cursor-pointer bg-transparent p-0 text-left align-baseline font-semibold text-foreground underline decoration-current/20 underline-offset-4 wrap-anywhere hover:decoration-current/60 disabled:cursor-default',
        code &&
          'rounded-[0.25rem] bg-muted px-[0.1875rem] py-px font-mono text-[0.9em] font-normal no-underline hover:underline'
      )}
      data-slot="inline_path_link"
      disabled={opening}
      onClick={() => void open()}
      title={path}
      type="button"
    >
      {path}
    </button>
  )
}
