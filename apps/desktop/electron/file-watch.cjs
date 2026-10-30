// Change notifications for one file (the preview pane's live reload), the same
// on every OS (AIS-798).
//
// `fs.watch` on the parent directory is the fast path: it sees editors that
// save by rename, and it never needs `recursive` (unsupported on older Linux
// kernels/Node). It can still fail where the OS gives no watcher — Linux out
// of inotify watches (ENOSPC), too many open files (EMFILE), some network and
// container mounts — either by throwing or by emitting 'error' later. Both
// fall back to stat polling (`fs.watchFile`), so the preview keeps reloading
// instead of going stale or crashing the main process on an unhandled 'error'.

const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_POLL_INTERVAL_MS = 1000

function statChanged(curr, prev) {
  return curr.mtimeMs !== prev.mtimeMs || curr.size !== prev.size || curr.ino !== prev.ino
}

function watchFileForChanges(
  filePath,
  onChange,
  { fsImpl = fs, onFallback = null, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS } = {}
) {
  const targetName = path.basename(filePath)
  let watcher = null
  let pollListener = null
  let closed = false

  const closeWatcher = () => {
    try {
      watcher?.close()
    } catch {
      // Already closed.
    }
    watcher = null
  }

  const startPolling = error => {
    if (closed || pollListener) return
    closeWatcher()
    pollListener = (curr, prev) => {
      if (statChanged(curr, prev)) onChange()
    }
    fsImpl.watchFile(filePath, { interval: pollIntervalMs }, pollListener)
    onFallback?.(error)
  }

  try {
    watcher = fsImpl.watch(path.dirname(filePath), (_eventType, filename) => {
      // Some platforms report no filename; treat that as "maybe ours".
      const changedName = filename ? path.basename(String(filename)) : ''
      if (changedName && changedName !== targetName) return
      onChange()
    })
    watcher.on?.('error', startPolling)
  } catch (error) {
    startPolling(error)
  }

  return {
    get mode() {
      return pollListener ? 'poll' : 'watch'
    },
    close() {
      closed = true
      closeWatcher()
      if (pollListener) {
        fsImpl.unwatchFile(filePath, pollListener)
        pollListener = null
      }
    }
  }
}

module.exports = { DEFAULT_POLL_INTERVAL_MS, watchFileForChanges }
