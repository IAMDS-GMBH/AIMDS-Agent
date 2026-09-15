// Boot-failure signatures for the desktop (AIS-344, B3).
//
// A JS copy of the boot subset of hermes_cli/support_signatures.py: the
// desktop classifies a failed or delayed boot from its own recent log lines
// (the venv may not even exist yet) and reports it as
// `boot-failure-<signature>` / `boot-recovered-<signature>`, so different
// causes get different cases and the per-kind rate limit. The ids are pinned
// to the Python catalog by boot-signatures.test.cjs.

const BOOT_SIGNATURES = [
  {
    id: 'boot.port_in_use',
    regex: /address already in use|EADDRINUSE|Errno 10048|\[Errno 48\]|\[Errno 98\]/i,
    title: 'Backend port already in use'
  },
  {
    id: 'boot.backend_exited_before_ready',
    regex: /backend exited before it became ready/i,
    title: 'Backend exited before it became ready'
  },
  {
    id: 'boot.backend_not_ready',
    regex: /backend did not become ready|Desktop boot failed|Backend bind race detected/i,
    title: 'Desktop boot failed or retried'
  },
  {
    id: 'python.traceback',
    regex: /Traceback \(most recent call last\)/i,
    title: 'Python traceback'
  },
  {
    id: 'generic.error',
    regex: /\b(?:ERROR|CRITICAL)\b|\berror\b/i,
    title: 'Error lines'
  }
]

const FALLBACK = { id: 'boot.unknown', title: 'Desktop boot failed' }

/** Signature id → rate-limit slug fragment (`boot-failure-port-in-use`). */
function signatureSlug(id) {
  return String(id || FALLBACK.id)
    .replace(/^boot\./, '')
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
}

/**
 * Most specific signature that matches anywhere in `text`; `generic.error`
 * only when nothing else does; `boot.unknown` when nothing matches at all.
 */
function classifyBootFailure(text) {
  const haystack = String(text || '')
  let fallback = null
  for (const sig of BOOT_SIGNATURES) {
    if (!sig.regex.test(haystack)) continue
    if (sig.id === 'generic.error') {
      fallback = sig
      continue
    }
    return { id: sig.id, title: sig.title, slug: signatureSlug(sig.id) }
  }
  if (fallback) return { id: fallback.id, title: fallback.title, slug: signatureSlug(fallback.id) }
  return { ...FALLBACK, slug: signatureSlug(FALLBACK.id) }
}

/** Last line of `text` that matches any signature — the one-line detail. */
function lastSignatureLine(text) {
  const lines = String(text || '')
    .split(/\r?\n/)
    .map(l => l.trim())
    .filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    for (const sig of BOOT_SIGNATURES) {
      if (sig.regex.test(lines[i])) return lines[i].slice(0, 400)
    }
  }
  return lines.length ? lines[lines.length - 1].slice(0, 400) : ''
}

const BOOT_MARKER = /Resolving Hermes backend|\[boot\] Resolving|Desktop starting|=== desktop start/i

/** Lines from the last boot marker on (desktop.log has no timestamps), ≤ maxLines. */
function lastBootSection(lines, maxLines = 300) {
  const list = Array.isArray(lines) ? lines : String(lines || '').split(/\r?\n/)
  let start = 0
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (BOOT_MARKER.test(list[i])) {
      start = i
      break
    }
  }
  return list.slice(start).slice(-maxLines)
}

module.exports = {
  BOOT_SIGNATURES,
  BOOT_SIGNATURE_IDS: BOOT_SIGNATURES.map(s => s.id),
  classifyBootFailure,
  lastBootSection,
  lastSignatureLine,
  signatureSlug
}
