import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { acknowledgeProfileSoulTemplate, type ProfileSoulTemplate } from '@/hermes'
import { useI18n } from '@/i18n'
import { Sparkles } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'

export type SoulTemplateInfo = ProfileSoulTemplate

// AIS-795: an edited SOUL.md is never overwritten by an update. When a newer
// template ships, this one line says so and lets the owner look at the
// differences, pull the template into the editor (saving stays their call) or
// dismiss the notice for this template version.
export function SoulTemplateNotice({
  onDismissed,
  onLoad,
  profileName,
  template
}: {
  onDismissed: () => void
  onLoad: (text: string) => void
  profileName: string
  template: SoulTemplateInfo
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [showDiff, setShowDiff] = useState(false)

  function dismiss() {
    onDismissed()
    void acknowledgeProfileSoulTemplate(profileName).catch(err => notifyError(err, p.failedSaveSoul))
  }

  return (
    <div
      className="space-y-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs"
      data-slot="soul-template-notice"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium text-foreground">{p.soulTemplateNotice}</span>
        <span className="text-muted-foreground">{p.soulTemplateHint}</span>
        <div className="ml-auto flex flex-wrap gap-1">
          {template.diff && (
            <Button onClick={() => setShowDiff(value => !value)} size="xs" variant="ghost">
              {showDiff ? p.soulTemplateHideDiff : p.soulTemplateShowDiff}
            </Button>
          )}
          {template.template && (
            <Button onClick={() => onLoad(template.template ?? '')} size="xs" variant="ghost">
              {p.soulTemplateLoad}
            </Button>
          )}
          <Button onClick={dismiss} size="xs" variant="ghost">
            {p.soulTemplateDismiss}
          </Button>
        </div>
      </div>
      {showDiff && template.diff && (
        <pre className="max-h-64 overflow-auto rounded-sm border border-border bg-background p-2 font-mono text-[0.7rem] leading-4">
          {template.diff.split('\n').map((line, index) => (
            <div
              className={cn(
                line.startsWith('+') && !line.startsWith('+++') && 'text-emerald-600 dark:text-emerald-400',
                line.startsWith('-') && !line.startsWith('---') && 'text-destructive'
              )}
              key={index}
            >
              {line || ' '}
            </div>
          ))}
        </pre>
      )}
    </div>
  )
}
