import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { DisableFeedbackPromptsDialog } from '@/components/disable-feedback-prompts-dialog'
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
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { AlertCircle, CheckCircle2, Globe, HelpCircle } from '@/lib/icons'
import { $feedbackPromptsEnabled, enableFeedbackPrompts } from '@/store/feedback-prompts'
import { notify } from '@/store/notifications'
import { addSupportTicket } from '@/store/support-tickets'

export interface ReportIssueDialogProps {
  contextType?: 'chat_session' | 'boot_error' | 'update_error' | 'install_error' | 'manual'
  defaultCategory?: string
  defaultSeverity?: string
  defaultSummary?: string
  installType?: 'fresh_install' | 'update'
  onOpenChange: (open: boolean) => void
  open: boolean
  sessionId?: string | null
}

export function ReportIssueDialog({
  contextType = 'manual',
  defaultCategory = 'other',
  defaultSeverity = 'medium',
  defaultSummary = '',
  installType,
  onOpenChange,
  open,
  sessionId
}: ReportIssueDialogProps) {
  const { t } = useI18n()
  const copy = t.reportIssue || {
    title: 'Problem melden',
    description: 'Senden Sie ein Fehlerprotokoll und Details direkt an das Support-Team.',
    categoryLabel: 'Kategorie',
    severityLabel: 'Schweregrad',
    summaryLabel: 'Zusammenfassung',
    summaryPlaceholder: 'Kurze Beschreibung des Problems',
    detailsLabel: 'Details / Beschreibung',
    detailsPlaceholder: 'Was ist passiert? Welche Schritte führen zum Fehler?',
    attachSession: 'Chat-Verlauf und Diagnose-Logs anhängen',
    submit: 'Problem absenden',
    submitting: 'Wird gesendet…',
    successTitle: 'Problem erfolgreich gemeldet!',
    successMessage: 'Ihr Support-Ticket wurde erstellt:',
    referenceId: 'Referenz-ID',
    diagnosticsLocationHint: 'Sie finden den Status Ihrer Tickets jederzeit in den Einstellungen unter ⚙️ Diagnose (Zahnrad).',
    translateToEnglish: 'In Englisch übersetzen',
    translating: 'Wird übersetzt…',
    translateSuccess: 'Erfolgreich ins Englische übersetzt.',
    close: 'Schließen',
    errorTitle: 'Senden fehlgeschlagen',
    categories: {
      chat_issue: 'Chat & Antworten (Problem im Chat / KI antwortet nicht)',
      mcp_tools: 'MCP & Tools (Werkzeug oder Server nicht gefunden / fehlerhaft)',
      ui_bug: 'Benutzeroberfläche & Anzeige (Layout, Buttons, Formatierung)',
      llm_timeout: 'KI-Verbindung & Timeout (Antwort bricht ab / Abbruch)',
      connection_error: 'Netzwerk & Gateway (Verbindung zum Server fehlgeschlagen)',
      performance: 'Performance & Tempo (Client langsam / hohe Auslastung)',
      installation_update: 'Installation & Updates (Client-Start / Update-Fehler)',
      feature_request: 'Verbesserungsvorschlag & Idee (Neues Feature)',
      other: 'Sonstiges'
    },
    severities: {
      low: 'Niedrig',
      medium: 'Mittel',
      high: 'Hoch',
      critical: 'Kritisch'
    }
  }

  const promptCopy = t.feedbackPrompts || {
    toggleLabel: 'Regelmäßige Feedback-Hinweise',
    toggleDesc: 'Gelegentliche Erinnerung im Client zur Abgabe von Feedback'
  }

  const feedbackPromptsEnabled = useStore($feedbackPromptsEnabled)
  const [disableDialogOpen, setDisableDialogOpen] = useState(false)

  const [category, setCategory] = useState(defaultCategory)
  const [severity, setSeverity] = useState(defaultSeverity)
  const [summary, setSummary] = useState(defaultSummary)
  const [description, setDescription] = useState('')
  const [includeSession, setIncludeSession] = useState(Boolean(sessionId))
  const [loading, setLoading] = useState(false)
  const [translating, setTranslating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [referenceId, setReferenceId] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSummary(defaultSummary)
      setCategory(defaultCategory)
      setSeverity(defaultSeverity)
      setIncludeSession(Boolean(sessionId))
    }
  }, [open, defaultSummary, defaultCategory, defaultSeverity, sessionId])

  useEffect(() => {
    const handleOpenEvent = (e: Event) => {
      const customEvent = e as CustomEvent<{ category?: string; summary?: string }>
      if (customEvent.detail) {
        if (customEvent.detail.category) setCategory(customEvent.detail.category)
        if (customEvent.detail.summary) setSummary(customEvent.detail.summary)
      }
      onOpenChange(true)
    }

    window.addEventListener('hermes:open-report-issue', handleOpenEvent)
    return () => {
      window.removeEventListener('hermes:open-report-issue', handleOpenEvent)
    }
  }, [onOpenChange])

  const handleTranslate = async () => {
    if (!summary.trim() && !description.trim()) return
    setTranslating(true)
    try {
      const desktop = window.hermesDesktop
      if (desktop?.api) {
        const res = await desktop.api<{ summary?: string; description?: string }>({
          path: '/api/translate',
          method: 'POST',
          body: { summary: summary.trim(), description: description.trim(), target_lang: 'en' }
        }).catch(() => null)

        if (res?.summary || res?.description) {
          if (res.summary) setSummary(res.summary)
          if (res.description) setDescription(res.description)
          notify({ kind: 'success', message: copy.translateSuccess || 'Erfolgreich ins Englische übersetzt.' })
          return
        }
      }

      notify({ kind: 'info', message: 'Übersetzungsservice ist aktuell nicht erreichbar.' })
    } catch {
      notify({ kind: 'warning', message: 'Übersetzung fehlgeschlagen.' })
    } finally {
      setTranslating(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!summary.trim()) return

    setLoading(true)
    setError(null)

    try {
      const desktop = window.hermesDesktop
      const fn = desktop?.reportIssue || desktop?.sendSupportLogs

      if (!fn) {
        throw new Error('Hermes Desktop Support-API nicht verfügbar.')
      }

      const res = await fn({
        category,
        severity,
        summary: summary.trim(),
        userDescription: description.trim(),
        sessionId: includeSession && sessionId ? sessionId : undefined,
        clientType: 'hermes-desktop',
        contextType,
        installType,
        reason: 'user_issue_report'
      } as any)

      if (res.ok) {
        const refId = res.reference_id || res.referenceId || 'SUP-SUCCESS'
        setReferenceId(refId)
        addSupportTicket({
          jobId: (res as any).job_id || (res as any).jobId || refId,
          caseId: (res as any).support_case_id || refId,
          referenceId: refId,
          summary: summary.trim(),
          category,
          severity,
          createdAt: Date.now()
        })
      } else {
        setError(res.error || 'Upload vom Support-Server abgelehnt.')
      }
    } catch (err: any) {
      setError(err?.message || 'Unerwarteter Fehler beim Senden.')
    } finally {
      setLoading(false)
    }
  }

  const handleClose = () => {
    if (loading) return
    onOpenChange(false)
    // Reset state after dialog closes
    setTimeout(() => {
      setError(null)
      setReferenceId(null)
      setSummary(defaultSummary)
      setDescription('')
    }, 200)
  }

  return (
    <>
      <Dialog onOpenChange={handleClose} open={open}>
        <DialogContent className="max-w-md gap-4 p-5">
          <DialogHeader>
            <DialogTitle icon={HelpCircle}>{copy.title}</DialogTitle>
            <DialogDescription>{copy.description}</DialogDescription>
          </DialogHeader>

          {referenceId ? (
            <div className="flex flex-col items-center gap-3 py-4 text-center">
              <CheckCircle2 className="size-10 text-emerald-500" />
              <div className="text-sm font-medium text-foreground">{copy.successTitle}</div>
              <div className="text-xs text-muted-foreground">{copy.successMessage}</div>
              <div className="rounded-md border border-border bg-accent/30 px-3 py-1.5 font-mono text-xs font-semibold text-primary">
                {copy.referenceId}: {referenceId}
              </div>
              <p className="max-w-xs text-center text-[11px] text-muted-foreground">
                {copy.diagnosticsLocationHint || 'Sie finden den Status Ihrer Tickets jederzeit in den Einstellungen unter Gateway → Support-Tickets.'}
              </p>
              <DialogFooter className="mt-4 w-full">
                <Button className="w-full" onClick={handleClose}>
                  {copy.close}
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <form className="flex flex-col gap-3.5" onSubmit={handleSubmit}>
              {error && (
                <div className="flex items-start gap-2.5 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="size-4 shrink-0" />
                  <div>
                    <div className="font-semibold">{copy.errorTitle}</div>
                    <div>{error}</div>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">{copy.categoryLabel}</label>
                  <select
                    className="h-8 rounded-md border border-input bg-transparent px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    onChange={e => setCategory(e.target.value)}
                    value={category}
                  >
                    <option value="chat_issue">{copy.categories.chat_issue}</option>
                    <option value="mcp_tools">{copy.categories.mcp_tools}</option>
                    <option value="ui_bug">{copy.categories.ui_bug}</option>
                    <option value="llm_timeout">{copy.categories.llm_timeout}</option>
                    <option value="connection_error">{copy.categories.connection_error}</option>
                    <option value="performance">{copy.categories.performance}</option>
                    <option value="installation_update">{copy.categories.installation_update}</option>
                    <option value="feature_request">{copy.categories.feature_request}</option>
                    <option value="other">{copy.categories.other}</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">{copy.severityLabel}</label>
                  <select
                    className="h-8 rounded-md border border-input bg-transparent px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    onChange={e => setSeverity(e.target.value)}
                    value={severity}
                  >
                    <option value="low">{copy.severities.low}</option>
                    <option value="medium">{copy.severities.medium}</option>
                    <option value="high">{copy.severities.high}</option>
                    <option value="critical">{copy.severities.critical}</option>
                  </select>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-foreground">{copy.summaryLabel}</label>
                <Input
                  className="h-8 text-xs"
                  onChange={e => setSummary(e.target.value)}
                  placeholder={copy.summaryPlaceholder}
                  required
                  value={summary}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between gap-1">
                  <label className="text-xs font-medium text-foreground">{copy.detailsLabel}</label>
                  <Button
                    className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-foreground"
                    disabled={translating || (!summary.trim() && !description.trim())}
                    onClick={handleTranslate}
                    size="xs"
                    type="button"
                    variant="ghost"
                  >
                    <Globe className="mr-1 size-3" />
                    {translating ? (copy.translating || 'Wird übersetzt…') : (copy.translateToEnglish || 'In Englisch übersetzen')}
                  </Button>
                </div>
                <Textarea
                  className="min-h-[80px] text-xs resize-y"
                  onChange={e => setDescription(e.target.value)}
                  placeholder={copy.detailsPlaceholder}
                  value={description}
                />
              </div>

              {sessionId && (
                <label className="flex items-center gap-2 cursor-pointer pt-1 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    className="rounded border-input text-primary focus:ring-primary"
                    checked={includeSession}
                    onChange={e => setIncludeSession(e.target.checked)}
                  />
                  {copy.attachSession}
                </label>
              )}

              <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/20 p-2.5 text-xs">
                <div className="flex flex-col">
                  <span className="font-medium text-foreground">{promptCopy.toggleLabel}</span>
                  <span className="text-[0.7rem] text-muted-foreground">{promptCopy.toggleDesc}</span>
                </div>
                <Switch
                  checked={feedbackPromptsEnabled}
                  onCheckedChange={checked => {
                    if (!checked) {
                      setDisableDialogOpen(true)
                    } else {
                      enableFeedbackPrompts()
                    }
                  }}
                  size="xs"
                />
              </div>

              <DialogFooter className="mt-2 pt-2 border-t border-border">
                <Button disabled={loading} onClick={handleClose} type="button" variant="outline">
                  {copy.close}
                </Button>
                <Button disabled={loading || !summary.trim()} type="submit">
                  {loading ? copy.submitting : copy.submit}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <DisableFeedbackPromptsDialog open={disableDialogOpen} onOpenChange={setDisableDialogOpen} />
    </>
  )
}
