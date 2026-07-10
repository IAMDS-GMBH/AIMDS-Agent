import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Copy, ExternalLink, Loader2 } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'
import { cn } from '@/lib/utils'
import { useGatewayRequest } from '../gateway/hooks/use-gateway-request'

interface OutlookAuthStartResult {
  request_id: string
  verification_uri: string
  user_code: string
  expires_in: number
  flow?: 'loopback' | 'device_code'
  resolved_request_body?: Record<string, unknown>
}

interface OutlookAuthStatusResult {
  status: 'pending' | 'success' | 'error' | 'expired'
  access_token?: string
  error?: string
}

interface OutlookAuthModalProps {
  open: boolean
  tenantId: string
  clientId: string
  clientSecret?: string
  useSavedEnv?: boolean
  onComplete: (accessToken: string) => void
  onCancel: () => void
}

export function OutlookAuthModal({
  open,
  tenantId,
  clientId,
  clientSecret = '',
  useSavedEnv = false,
  onComplete,
  onCancel
}: OutlookAuthModalProps) {
  const [stage, setStage] = useState<'initiating' | 'waiting' | 'success' | 'error'>('initiating')
  const [verificationUri, setVerificationUri] = useState('')
  const [userCode, setUserCode] = useState('')
  const [expiresIn, setExpiresIn] = useState(0)
  const [flow, setFlow] = useState<'loopback' | 'device_code'>('device_code')
  const [error, setError] = useState('')
  const [requestDebug, setRequestDebug] = useState('')
  const [requestId, setRequestId] = useState('')
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null)
  const [copied, setCopied] = useState(false)
  const { requestGateway } = useGatewayRequest()

  useEffect(() => {
    if (!open) {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
      return
    }

    // Reset state when modal opens
    setStage('initiating')
    setVerificationUri('')
    setUserCode('')
    setError('')
    setRequestDebug('')
    setRequestId('')
    setFlow('device_code')
    setCopied(false)

    // Initiate device code flow via TUI gateway RPC (only once per open)
    ;(async () => {
      try {
        // Validate required fields (skip if using saved env)
        if (!useSavedEnv && (!tenantId.trim() || !clientId.trim())) {
          const missing = []
          if (!tenantId.trim()) missing.push('Tenant ID')
          if (!clientId.trim()) missing.push('Client ID')
          throw new Error(`Missing required fields: ${missing.join(', ')}`)
        }

        console.log('[Outlook Auth] Initiating device code flow via gateway RPC:', {
          use_saved_env: useSavedEnv,
          tenant_id: useSavedEnv ? '(from .env)' : tenantId,
          client_id: useSavedEnv ? '(from .env)' : clientId,
          has_secret: useSavedEnv ? '(from .env)' : !!clientSecret
        })

        const requestData = useSavedEnv 
          ? { use_saved_env: true }
          : {
              tenant_id: tenantId,
              client_id: clientId,
              ...(clientSecret.trim() ? { client_secret: clientSecret } : {})
            }
        const requestDebugPayload = useSavedEnv
          ? { use_saved_env: true }
          : {
              tenant_id: tenantId,
              client_id: clientId,
              ...(clientSecret.trim() ? { client_secret: clientSecret } : {})
            }
        setRequestDebug(JSON.stringify(requestDebugPayload, null, 2))

        const data = await requestGateway<OutlookAuthStartResult>('outlook.auth.start', requestData)
        setRequestDebug(
          JSON.stringify((data.resolved_request_body ?? requestDebugPayload) as Record<string, unknown>, null, 2)
        )

        console.log('[Outlook Auth] Device code received:', {
          request_id: data.request_id,
          verification_uri: data.verification_uri,
          user_code: data.user_code,
          expires_in: data.expires_in
        })

        setRequestId(data.request_id)
        setVerificationUri(data.verification_uri)
        setUserCode(data.user_code)
        setExpiresIn(data.expires_in)
        setFlow(data.flow === 'loopback' ? 'loopback' : 'device_code')
        setStage('waiting')

        // Start polling for completion (use request_id from response, not regenerate)
        const currentRequestId = data.request_id
        const pollStartTime = Date.now()
        const maxPollDuration = (data.expires_in + 30) * 1000 // poll until expiry + 30s buffer

        const interval = setInterval(async () => {
          // Safety: stop polling if we've been polling longer than device code lifetime
          if (Date.now() - pollStartTime > maxPollDuration) {
            clearInterval(interval)
            console.warn('[Outlook Auth] Polling timeout (device code expired)')
            setStage('error')
            setError('Device code expired. Please try again.')
            return
          }

          try {
            const status = await requestGateway<OutlookAuthStatusResult>('outlook.auth.status', {
              request_id: currentRequestId
            })
            console.log('[Outlook Auth] Poll response:', status)

            if (status.status === 'success') {
              console.log('[Outlook Auth] Authentication successful')
              clearInterval(interval)
              setStage('success')
              setTimeout(() => {
                onComplete(status.access_token ?? '')
              }, 1000)
            } else if (status.status === 'error') {
              console.error('[Outlook Auth] Backend error:', status.error)
              clearInterval(interval)
              setStage('error')
              setError(status.error || 'Authentication failed')
              setRequestDebug(prev =>
                prev
                  ? `${prev}\n\nstatus_poll_request:\n${JSON.stringify({ request_id: currentRequestId }, null, 2)}`
                  : JSON.stringify({ status_poll_request: { request_id: currentRequestId } }, null, 2)
              )
            } else if (status.status === 'expired') {
              console.warn('[Outlook Auth] Device code expired')
              clearInterval(interval)
              setStage('error')
              setError('Device code expired. Please try again.')
            }
          } catch (err) {
            console.error('[Outlook Auth] Poll error:', err)
          }
        }, 2000)

        pollIntervalRef.current = interval
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Failed to initiate authentication'
        console.error('[Outlook Auth] Error during initialization:', errorMsg, err)
        setStage('error')
        setError(errorMsg)
      }
    })()

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [open]) // Only depend on 'open' to prevent re-initialization

  const handleCopyCode = async () => {
    try {
      await navigator.clipboard.writeText(userCode)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      notify({ kind: 'success', title: 'Code copied', message: 'Device code copied to clipboard' })
    } catch (err) {
      notifyError(err, 'Failed to copy code')
    }
  }

  const handleOpenBrowser = () => {
    window.open(verificationUri, '_blank')
  }

  return (
    <Dialog open={open && stage !== 'success'} onOpenChange={open => !open && onCancel()}>
      <DialogContent showCloseButton={stage !== 'waiting'}>
        <DialogHeader>
          <DialogTitle>
            {stage === 'initiating' && 'Initiating Outlook Authentication'}
            {stage === 'waiting' && 'Complete Outlook Authentication'}
            {stage === 'error' && 'Authentication Failed'}
          </DialogTitle>
        </DialogHeader>

        <DialogDescription asChild>
          <div className="space-y-4">
            {stage === 'initiating' && (
              <div className="flex items-center justify-center gap-2">
                <Loader2 className="size-4 animate-spin" />
                <span>Preparing authentication...</span>
              </div>
            )}

            {stage === 'waiting' && flow === 'loopback' && (
              <>
                <p>Click the button below to sign in with Microsoft in your browser.</p>
                <Button onClick={handleOpenBrowser} className="w-full" variant="default">
                  <ExternalLink className="size-4" />
                  Open Microsoft Login
                </Button>

                <p className="text-xs text-muted-foreground">
                  No code to enter — waiting for you to finish signing in in your browser
                  (expires in {Math.ceil(expiresIn / 60)} minutes)...
                </p>
              </>
            )}

            {stage === 'waiting' && flow === 'device_code' && (
              <>
                <p>1. Click the button below to open Microsoft login in your browser</p>
                <Button onClick={handleOpenBrowser} className="w-full" variant="default">
                  <ExternalLink className="size-4" />
                  Open Microsoft Login
                </Button>

                <div className="rounded-lg bg-muted p-4">
                  <p className="mb-2 text-sm font-medium">2. Enter this code when prompted:</p>
                  <div className="flex gap-2">
                    <Input
                      readOnly
                      value={userCode}
                      className="font-mono text-lg font-bold tracking-widest"
                    />
                    <Button
                      onClick={handleCopyCode}
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </div>

                <p className="text-xs text-muted-foreground">
                  Code expires in {Math.ceil(expiresIn / 60)} minutes. Waiting for completion...
                </p>
              </>
            )}

            {stage === 'error' && (
              <div className="rounded-lg bg-destructive/10 p-4 text-destructive">
                <p className="font-medium">Authentication Error</p>
                <p className="mt-1 text-sm">{error}</p>
                {requestDebug && (
                  <div className="mt-3">
                    <p className="text-xs font-medium uppercase tracking-wide">Request body sent</p>
                    <pre className="mt-1 overflow-x-auto rounded bg-background/70 p-2 text-xs text-foreground">
                      {requestDebug}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </DialogDescription>

        <DialogFooter>
          {stage === 'error' && (
            <Button variant="outline" onClick={onCancel}>
              Close
            </Button>
          )}
          {stage !== 'error' && stage !== 'success' && (
            <Button variant="outline" onClick={onCancel} disabled={stage === 'waiting'}>
              Cancel
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
