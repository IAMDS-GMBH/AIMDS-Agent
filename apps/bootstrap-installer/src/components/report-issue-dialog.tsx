import { useState, useEffect, useRef } from 'react'
import { Dialog } from 'radix-ui'
import { invoke } from '@tauri-apps/api/core'
import { Button } from './button'
import { AlertCircle, CheckCircle2, HelpCircle, ImageIcon, Loader2, X } from 'lucide-react'

interface AttachedFile {
  id: string
  name: string
  dataUrl: string
  size: number
}

export interface ReportIssueDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultSummary?: string
  defaultCategory?: string
  defaultSeverity?: string
  errorMessage?: string | null
  installType?: 'fresh_install' | 'update'
  contextType?: 'install_error' | 'update_error' | 'manual'
}

interface SupportTicketResult {
  ok: boolean
  reference_id?: string | null
  support_case_id?: string | null
  error?: string | null
}

export function ReportIssueDialog({
  open,
  onOpenChange,
  defaultSummary = '',
  defaultCategory = 'installation_update',
  defaultSeverity = 'high',
  errorMessage,
  installType = 'fresh_install',
  contextType = 'install_error',
}: ReportIssueDialogProps) {
  const lang = typeof navigator !== 'undefined' ? (navigator.language || '').toLowerCase() : ''
  const isDe = lang.startsWith('de')

  const copy = {
    title: isDe ? 'Problem melden' : 'Report an Issue',
    description: isDe
      ? 'Senden Sie ein Fehlerprotokoll und Details direkt an das AIMDS Support-Team.'
      : 'Send error logs and diagnostics directly to the AIMDS support team.',
    categoryLabel: isDe ? 'Kategorie' : 'Category',
    severityLabel: isDe ? 'Schweregrad' : 'Severity',
    summaryLabel: isDe ? 'Zusammenfassung' : 'Summary',
    summaryPlaceholder: isDe ? 'Kurze Beschreibung des Fehlers' : 'Short description of the problem',
    detailsLabel: isDe ? 'Details & Schritte zur Wiederholung' : 'Details & steps to reproduce',
    detailsPlaceholder: isDe
      ? 'Was ist passiert? Welche Fehlermeldung ist aufgetreten?'
      : 'What happened? What error message occurred?',
    screenshotsLabel: isDe ? 'Screenshots / Bilder' : 'Screenshots / Images',
    selectScreenshot: isDe ? 'Screenshot auswählen' : 'Select Screenshot',
    pasteHint: isDe
      ? 'Screenshot einfügen (Strg+V / Cmd+V) oder Datei auswählen'
      : 'Paste screenshot (Ctrl+V / Cmd+V) or select file',
    attachLogs: isDe
      ? 'Installations- und Systemprotokolle anhängen (empfohlen)'
      : 'Attach installation and system logs (recommended)',
    submit: isDe ? 'Problem absenden' : 'Submit Report',
    submitting: isDe ? 'Wird gesendet…' : 'Submitting…',
    successTitle: isDe ? 'Problem erfolgreich gemeldet!' : 'Issue successfully reported!',
    successMessage: isDe
      ? 'Ihr Support-Ticket wurde erstellt und an das AIMDS-Team übermittelt:'
      : 'Your support ticket was created and submitted to the AIMDS team:',
    referenceId: isDe ? 'Referenz-ID' : 'Reference ID',
    supportHint: isDe
      ? 'Unser Support-Team analysiert die Diagnoseprotokolle und prüft den Fall.'
      : 'Our support team will analyze the diagnostic logs and investigate.',
    close: isDe ? 'Schließen' : 'Close',
    errorTitle: isDe ? 'Senden fehlgeschlagen' : 'Submission failed',
    categories: {
      installation_update: isDe ? 'Installation & Update' : 'Installation & Update',
      connection_error: isDe ? 'Netzwerk & Verbindung' : 'Network & Connection',
      ui_bug: isDe ? 'Benutzeroberfläche & Anzeige' : 'UI & Display',
      mcp_tools: isDe ? 'MCP & Werkzeuge' : 'MCP & Tools',
      other: isDe ? 'Sonstiges' : 'Other',
    },
    severities: {
      low: isDe ? 'Niedrig' : 'Low',
      medium: isDe ? 'Mittel' : 'Medium',
      high: isDe ? 'Hoch' : 'High',
      critical: isDe ? 'Kritisch' : 'Critical',
    },
  }

  const [category, setCategory] = useState(defaultCategory)
  const [severity, setSeverity] = useState(defaultSeverity)
  const [summary, setSummary] = useState(defaultSummary)
  const [description, setDescription] = useState('')
  const [includeLogs, setIncludeLogs] = useState(true)
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [referenceId, setReferenceId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (open) {
      setSummary(defaultSummary || (errorMessage ? `Installationsfehler: ${errorMessage.slice(0, 80)}` : ''))
      setCategory(defaultCategory)
      setSeverity(defaultSeverity)
      setIncludeLogs(true)
      setAttachments([])
      setError(null)
      setReferenceId(null)
    }
  }, [open, defaultSummary, defaultCategory, defaultSeverity, errorMessage])

  const addFile = (file: File) => {
    if (!file.type.startsWith('image/')) return
    if (file.size > 10 * 1024 * 1024) return
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      setAttachments(prev => [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          name: file.name || `screenshot_${prev.length + 1}.png`,
          dataUrl,
          size: file.size,
        },
      ])
    }
    reader.readAsDataURL(file)
  }

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile()
        if (file) {
          addFile(file)
          e.preventDefault()
        }
      }
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    for (let i = 0; i < files.length; i++) {
      addFile(files[i])
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!summary.trim()) return

    setLoading(true)
    setError(null)

    try {
      const res = await invoke<SupportTicketResult>('submit_support_ticket', {
        payload: {
          category,
          severity,
          summary: summary.trim(),
          user_description: description.trim() || undefined,
          include_logs: includeLogs,
          install_type: installType,
          context_type: contextType,
          error_message: errorMessage || undefined,
          attachments: attachments.map(a => a.dataUrl),
        },
      })

      if (res.ok) {
        setReferenceId(res.reference_id || res.support_case_id || 'SUP-RECEIVED')
      } else {
        setError(res.error || 'Server lehnte das Fehlerprotokoll ab.')
      }
    } catch (err: any) {
      setError(err?.message || String(err) || 'Unerwarteter Fehler beim Senden.')
    } finally {
      setLoading(false)
    }
  }

  const handleClose = () => {
    if (loading) return
    onOpenChange(false)
    setTimeout(() => {
      setError(null)
      setReferenceId(null)
    }, 200)
  }

  return (
    <Dialog.Root open={open} onOpenChange={openState => { if (!openState) handleClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-xs transition-opacity" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-background p-6 shadow-2xl focus:outline-none">
          <div className="flex items-center justify-between pb-3 border-b border-border/70">
            <div className="flex items-center gap-2">
              <HelpCircle className="size-5 text-primary" />
              <Dialog.Title className="text-base font-semibold text-foreground">
                {copy.title}
              </Dialog.Title>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                aria-label="Close"
              >
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>

          <Dialog.Description className="mt-2 text-xs text-muted-foreground">
            {copy.description}
          </Dialog.Description>

          {referenceId ? (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <CheckCircle2 className="size-12 text-emerald-500" />
              <div className="text-base font-medium text-foreground">{copy.successTitle}</div>
              <div className="text-xs text-muted-foreground">{copy.successMessage}</div>
              <div className="rounded-md border border-border bg-accent/40 px-4 py-2 font-mono text-sm font-semibold text-primary select-all">
                {copy.referenceId}: {referenceId}
              </div>
              <p className="max-w-xs text-center text-[11px] text-muted-foreground mt-2">
                {copy.supportHint}
              </p>
              <div className="mt-4 w-full">
                <Button className="w-full" onClick={handleClose}>
                  {copy.close}
                </Button>
              </div>
            </div>
          ) : (
            <form className="mt-4 flex flex-col gap-3.5" onPaste={handlePaste} onSubmit={handleSubmit}>
              {error && (
                <div className="flex items-start gap-2.5 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="size-4 shrink-0 mt-0.5" />
                  <div>
                    <div className="font-semibold">{copy.errorTitle}</div>
                    <div className="text-[11px] mt-0.5">{error}</div>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">{copy.categoryLabel}</label>
                  <select
                    className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    onChange={e => setCategory(e.target.value)}
                    value={category}
                  >
                    <option value="installation_update">{copy.categories.installation_update}</option>
                    <option value="connection_error">{copy.categories.connection_error}</option>
                    <option value="ui_bug">{copy.categories.ui_bug}</option>
                    <option value="mcp_tools">{copy.categories.mcp_tools}</option>
                    <option value="other">{copy.categories.other}</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-medium text-foreground">{copy.severityLabel}</label>
                  <select
                    className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
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
                <input
                  type="text"
                  className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                  onChange={e => setSummary(e.target.value)}
                  placeholder={copy.summaryPlaceholder}
                  required
                  value={summary}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-foreground">{copy.detailsLabel}</label>
                <textarea
                  className="min-h-[85px] rounded-md border border-input bg-background p-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-y"
                  onChange={e => setDescription(e.target.value)}
                  placeholder={copy.detailsPlaceholder}
                  value={description}
                />
              </div>

              {/* Screenshots / Attachments section */}
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-foreground">{copy.screenshotsLabel}</label>
                  <input
                    accept="image/*"
                    className="hidden"
                    multiple
                    onChange={handleFileChange}
                    ref={fileInputRef}
                    type="file"
                  />
                  <Button
                    className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
                    onClick={() => fileInputRef.current?.click()}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    <ImageIcon className="mr-1 size-3" />
                    {copy.selectScreenshot}
                  </Button>
                </div>

                {attachments.length > 0 ? (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {attachments.map(att => (
                      <div
                        className="group relative flex items-center gap-1.5 rounded border border-border bg-muted/40 p-1 pr-2 text-[11px]"
                        key={att.id}
                      >
                        <img
                          alt={att.name}
                          className="size-8 rounded object-cover border border-border/50"
                          src={att.dataUrl}
                        />
                        <div className="flex flex-col max-w-[120px]">
                          <span className="truncate font-medium text-foreground">{att.name}</span>
                          <span className="text-[9px] text-muted-foreground">
                            {Math.round(att.size / 1024)} KB
                          </span>
                        </div>
                        <button
                          className="ml-1 rounded p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          onClick={() => removeAttachment(att.id)}
                          type="button"
                        >
                          <X className="size-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div
                    className="flex items-center justify-center rounded border border-dashed border-border/70 bg-muted/10 py-2 text-center text-[11px] text-muted-foreground cursor-pointer hover:bg-muted/20"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <span>{copy.pasteHint}</span>
                  </div>
                )}
              </div>

              <label className="flex items-center gap-2 cursor-pointer pt-1 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  className="rounded border-input text-primary focus:ring-primary"
                  checked={includeLogs}
                  onChange={e => setIncludeLogs(e.target.checked)}
                />
                {copy.attachLogs}
              </label>

              <div className="mt-3 flex justify-end gap-2 border-t border-border/70 pt-3">
                <Button disabled={loading} onClick={handleClose} type="button" variant="outline">
                  {copy.close}
                </Button>
                <Button disabled={loading || !summary.trim()} type="submit" className="min-w-[120px]">
                  {loading ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="size-3.5 animate-spin" />
                      {copy.submitting}
                    </span>
                  ) : (
                    copy.submit
                  )}
                </Button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
