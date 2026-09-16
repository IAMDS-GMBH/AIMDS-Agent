// AIS-352: ask macOS for access to the Hermes workspace BEFORE the backend
// spawns.
//
// The Vault lives under ~/Documents. On macOS the first access to that folder
// blocks synchronously until the user answers the TCC prompt ("Hermes möchte
// auf Dateien im Ordner Dokumente zugreifen"). When the Python backend was the
// first to touch it, the prompt appeared while the desktop was already waiting
// for the backend — the old 45 s readiness deadline gave up, the retry
// SIGTERMed the still-blocked backend, and the dialog vanished with the
// process before anyone could click "Allow" (2026-09-16, 7 boot cycles).
//
// The main process never gets killed, so a probe from here keeps the dialog
// on screen until it is answered; the child then inherits the grant.
const fs = require('node:fs')
const path = require('node:path')

// After this long without an answer the caller shows the "click Allow" hint.
const SLOW_ACCESS_MS = 1500
const PERMISSION_ERROR_CODES = new Set(['EPERM', 'EACCES'])

function permissionGuidance(dir) {
  return (
    `macOS denied Hermes access to ${dir}. Allow it under System Settings → Privacy & Security → ` +
    'Files and Folders → Hermes → Documents Folder, then start Hermes again.'
  )
}

async function readdirWithParentFallback(fsp, dir) {
  try {
    await fsp.readdir(dir)
  } catch (error) {
    // The Vault may not exist yet on a fresh install: its parent (Documents)
    // is what the permission is about.
    if (error?.code !== 'ENOENT') throw error
    await fsp.readdir(path.dirname(dir))
  }
}

/**
 * Probe `dir` (the Vault). Resolves { ok, skipped, slow, dir, waited_ms } —
 * `ok: false` carries `code` + a `message` with the System Settings path.
 * `onSlow` fires once when the probe exceeds `slowMs` (dialog probably up).
 * Non-macOS platforms and a missing dir are a no-op.
 */
async function ensureVaultAccess({
  dir,
  platform = process.platform,
  fsp = fs.promises,
  slowMs = SLOW_ACCESS_MS,
  onSlow = () => {},
  log = () => {},
  now = () => Date.now()
}) {
  if (platform !== 'darwin' || !dir) {
    return { ok: true, skipped: true, slow: false, dir: dir || null, waited_ms: 0 }
  }
  const started = now()
  let slow = false
  const timer = setTimeout(() => {
    slow = true
    log(`[boot] access to ${dir} is taking longer than ${slowMs} ms — a macOS permission dialog is probably waiting for an answer`)
    try {
      onSlow()
    } catch {
      void 0
    }
  }, slowMs)
  try {
    await readdirWithParentFallback(fsp, dir)
    const waited = now() - started
    if (slow) log(`[boot] access to ${dir} granted after ${waited} ms`)
    return { ok: true, skipped: false, slow, dir, waited_ms: waited }
  } catch (error) {
    if (PERMISSION_ERROR_CODES.has(error?.code)) {
      log(`[boot] access to ${dir} denied (${error.code})`)
      return { ok: false, skipped: false, slow, dir, waited_ms: now() - started, code: error.code, message: permissionGuidance(dir) }
    }
    // Anything else (odd mount, race) is not a permission problem — the
    // backend creates or reports the folder itself.
    log(`[boot] access probe for ${dir} failed with ${error?.code || error?.message || error}; continuing`)
    return { ok: true, skipped: false, slow, dir, waited_ms: now() - started, error: String(error?.code || error?.message || error) }
  } finally {
    clearTimeout(timer)
  }
}

module.exports = { PERMISSION_ERROR_CODES, SLOW_ACCESS_MS, ensureVaultAccess, permissionGuidance }
