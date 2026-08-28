import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface OutlookSetupGuideModalProps {
  open: boolean
  onClose: () => void
}

const OUTLOOK_SETUP_HTML = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; color: #111827; line-height: 1.5; }
      h2 { margin: 0 0 8px; font-size: 18px; }
      p { margin: 0 0 10px; font-size: 14px; }
      ul { margin: 0 0 12px 18px; padding: 0; }
      li { margin: 0 0 6px; font-size: 14px; }
      code { background: #f3f4f6; border-radius: 4px; padding: 2px 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
      .note { background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 8px; padding: 10px; margin-top: 10px; }
    </style>
  </head>
  <body>
    <h2>Outlook setup instructions</h2>
    <p>This summary is based on <code>docs/messaging/outlook-setup.md</code>.</p>
    <ul>
      <li>Contact your system administrator to get the <strong>Tenant ID</strong> and <strong>Client ID</strong> for the Azure app.</li>
      <li>Paste these values in Hermes under Messaging → Outlook and save changes.</li>
      <li>Authenticate with Outlook by clicking <strong>Test</strong> and completing Microsoft device-code login in your browser.</li>
      <li>You can also start authentication from chat with prompts like <em>Authenticate Outlook</em> or <em>Connect my Outlook account</em>.</li>
    </ul>
    <div class="note">
      Admin requirements: delegated Graph permissions (<code>Mail.Read</code>, <code>Mail.Send</code>, <code>Calendars.Read</code>, <code>offline_access</code>) and device flow enabled.
    </div>
  </body>
</html>`

export function OutlookSetupGuideModal({ open, onClose }: OutlookSetupGuideModalProps) {
  return (
    <Dialog onOpenChange={nextOpen => !nextOpen && onClose()} open={open}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Outlook setup</DialogTitle>
          <DialogDescription>Before using Outlook, ask your system administrator to enable and share the required IDs.</DialogDescription>
        </DialogHeader>
        <iframe
          className="h-[50vh] w-full rounded-md border bg-background"
          srcDoc={OUTLOOK_SETUP_HTML}
          title="Outlook setup instructions"
        />
        <DialogFooter>
          <Button onClick={onClose} variant="outline">
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
