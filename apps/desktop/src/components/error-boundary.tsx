import { Component, type ErrorInfo, type ReactNode, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { notify, notifyError } from '@/store/notifications'
import { addSupportTicket } from '@/store/support-tickets'

export interface ErrorBoundaryFallbackProps {
  componentStack?: string | null
  error: Error
  reset: () => void
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: (props: ErrorBoundaryFallbackProps) => ReactNode
  label?: string
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  componentStack: string | null
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { componentStack: null, error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { componentStack: null, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const tag = this.props.label ? `[error-boundary:${this.props.label}]` : '[error-boundary]'
    console.error(tag, error, info.componentStack)
    // Keep the stack on state: the support report is filed from the fallback,
    // which otherwise only sees the Error and would upload a case with no
    // indication of where the crash came from.
    this.setState({ componentStack: info.componentStack ?? null })
    this.props.onError?.(error, info)
  }

  reset = () => {
    this.setState({ componentStack: null, error: null })
  }

  render() {
    const { componentStack, error } = this.state

    if (!error) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback({ componentStack, error, reset: this.reset })
    }

    return <RootErrorFallback componentStack={componentStack} error={error} reset={this.reset} />
  }
}

/**
 * Flatten a caught render error into the support-case description.
 *
 * Without this the case arrives carrying only `reason:
 * 'renderer_error_boundary'` — it says a crash happened but not where.
 */
export function describeCrash(error: Error, componentStack?: string | null): string {
  const sections = [`${error.name}: ${error.message}`]

  if (error.stack) {
    sections.push(`Stack:\n${error.stack}`)
  }

  if (componentStack) {
    sections.push(`Component stack:\n${componentStack}`)
  }

  return sections.join('\n\n')
}

function RootErrorFallback({ componentStack, error, reset }: ErrorBoundaryFallbackProps) {
  const { t } = useI18n()
  const [sending, setSending] = useState(false)

  const sendSupportLogs = async () => {
    setSending(true)

    try {
      const desktop = window.hermesDesktop
      // reportIssue carries the crash details; sendSupportLogs is the older
      // channel and only ships logs, so it stays as the fallback.
      const fn = desktop?.reportIssue || desktop?.sendSupportLogs
      const summary = error.message ? `Anwendungsfehler: ${error.message}` : 'Anwendungsfehler'

      const result = await fn?.({
        category: 'ui_bug',
        severity: 'high',
        summary,
        userDescription: describeCrash(error, componentStack),
        clientType: 'hermes-desktop',
        contextType: 'crash',
        reason: 'renderer_error_boundary'
      } as any)

      if (result?.ok) {
        const reference = result.reference_id || result.referenceId
        addSupportTicket({
          jobId: reference || `job-${Date.now()}`,
          referenceId: reference,
          summary,
          category: 'ui_bug',
          severity: 'high',
          createdAt: Date.now()
        })
        notify({
          kind: 'success',
          title: 'Support logs sent',
          message: reference ? `Reference: ${reference}` : 'Diagnostic logs were uploaded for support.'
        })
      } else {
        notify({
          kind: 'warning',
          title: 'Support log upload failed',
          message: result?.error || 'Could not upload support logs.'
        })
      }
    } catch (err) {
      notifyError(err, 'Support log upload failed')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[1500] grid place-items-center bg-(--ui-chat-surface-background) p-6">
      <ErrorState
        className="w-full max-w-[28rem]"
        description={error.message || t.errors.boundaryDesc}
        title={t.errors.boundaryTitle}
      >
        <Button className="font-semibold" onClick={reset} size="lg">
          {t.common.retry}
        </Button>
        <Button onClick={() => window.location.reload()} variant="text">
          {t.errors.reloadWindow}
        </Button>
        <Button onClick={() => void window.hermesDesktop?.revealLogs()?.catch(() => undefined)} variant="text">
          {t.errors.openLogs}
        </Button>
        <Button disabled={sending} onClick={() => void sendSupportLogs()} variant="text">
          {sending ? 'Sending support logs…' : 'Send support logs'}
        </Button>
      </ErrorState>
    </div>
  )
}
