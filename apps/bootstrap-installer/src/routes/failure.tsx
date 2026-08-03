import { type CSSProperties } from 'react'
import { useStore } from '@nanostores/react'
import { openUrl } from '@tauri-apps/plugin-opener'
import { Button } from '../components/button'
import {
  $logPath,
  $mode,
  openLogDir,
  startInstall,
  startUpdate,
  type BootstrapStateModel
} from '../store'
import { RefreshCw, FileText, LifeBuoy } from 'lucide-react'

interface FailureProps {
  bootstrap: BootstrapStateModel
}

/*
 * Failure screen. Same hero treatment as Welcome/Success — the wordmark
 * carries the brand, so we keep it across every terminal state.
 *
 * The actual error message lives below in muted text. Three clear
 * affordances: Retry (primary), Open log folder (secondary), and Report issue.
 */
export default function Failure({ bootstrap }: FailureProps) {
  const logPath = useStore($logPath)
  const mode = useStore($mode)
  const isUpdate = mode === 'update'

  const lang = typeof navigator !== 'undefined' ? (navigator.language || '').toLowerCase() : ''
  const isDe = lang.startsWith('de')

  const titleText = isDe
    ? isUpdate ? 'Update nicht abgeschlossen' : 'Installation nicht abgeschlossen'
    : isUpdate ? 'Update didn\u2019t finish' : 'Install didn\u2019t finish'

  const retryText = isDe
    ? isUpdate ? 'Update erneut versuchen' : 'Installation erneut versuchen'
    : isUpdate ? 'Retry update' : 'Retry install'

  const logText = isDe ? 'Log-Ordner öffnen' : 'Open log folder'
  const reportText = isDe ? 'Problem melden' : 'Problem melden / Report issue'

  const handleReportIssue = () => {
    const url = 'https://github.com/NousResearch/Hermes-Agent/issues/new'
    void openUrl(url).catch(() => {
      window.open(url, '_blank')
    })
  }

  return (
    <div className="hermes-fade-in flex h-full flex-col items-center justify-center gap-6 px-12 py-10">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-destructive mix-blend-plus-lighter dark:text-destructive/90"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '5rem',
              '--fit-text-min': '2.25rem'
            } as CSSProperties
          }
        >
          <span>
            <span>{titleText}</span>
          </span>
          <span aria-hidden="true">{titleText}</span>
        </p>

        <p className="m-0 mx-auto max-w-xl text-center text-sm leading-normal tracking-tight text-muted-foreground">
          {bootstrap.error ??
            (isUpdate
              ? (isDe ? 'Beim Update ist ein Fehler aufgetreten.' : 'Something went wrong during the update.')
              : (isDe ? 'Bei der Installation ist ein Fehler aufgetreten.' : 'Something went wrong during installation.'))}
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button
          onClick={() => void (isUpdate ? startUpdate() : startInstall())}
          size="lg"
          className="inline-flex items-center gap-2 px-6"
        >
          <RefreshCw size={16} />
          {retryText}
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={() => void openLogDir()}
          className="inline-flex items-center gap-2"
        >
          <FileText size={16} />
          {logText}
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={handleReportIssue}
          className="inline-flex items-center gap-2"
        >
          <LifeBuoy size={16} />
          {reportText}
        </Button>
      </div>

      {logPath && (
        <p className="max-w-lg text-center text-xs text-muted-foreground/70">
          Log: <code className="font-mono">{logPath}</code>
        </p>
      )}
    </div>
  )
}
