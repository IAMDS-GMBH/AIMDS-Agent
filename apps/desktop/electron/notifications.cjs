// Pure helpers for native notifications + the unread badge (AIS-305). No
// Electron imports so `node --test` can exercise them directly; main.cjs wires
// the results into `Notification` / `app.setBadgeCount` / `setOverlayIcon`.

const NOTIFICATION_ACTION_KINDS = new Set(['cron-artifact'])

function trimmedString(value) {
  return typeof value === 'string' ? value.trim() : ''
}

// Validate the renderer-supplied click action. Only known kinds with the
// fields the renderer will read back survive; anything else yields null so a
// malformed payload degrades to a plain notification instead of a broken click.
function normalizeNotificationAction(action) {
  if (!action || typeof action !== 'object') return null
  const kind = trimmedString(action.kind)
  if (!NOTIFICATION_ACTION_KINDS.has(kind)) return null

  if (kind === 'cron-artifact') {
    const jobId = trimmedString(action.jobId)
    if (!jobId) return null
    const out = { kind, jobId }
    const path = trimmedString(action.path)
    const sessionId = trimmedString(action.sessionId)
    const profile = trimmedString(action.profile)
    if (path) out.path = path
    if (sessionId) out.sessionId = sessionId
    if (profile) out.profile = profile
    return out
  }

  return null
}

// Options for `new Notification(...)` plus the normalized click action (or
// null). Title falls back to the product name; body defaults to empty.
function buildNotificationOptions(payload, { defaultTitle = 'Hermes' } = {}) {
  const source = payload && typeof payload === 'object' ? payload : {}
  const title = trimmedString(source.title) || defaultTitle
  const body = typeof source.body === 'string' ? source.body : ''
  const action = normalizeNotificationAction(source.action)

  return {
    action,
    options: {
      title,
      body,
      silent: Boolean(source.silent)
    }
  }
}

// Clamp the renderer's unread count to a non-negative integer.
function normalizeBadgeCount(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return 0
  return Math.min(Math.floor(numeric), 9999)
}

// Whether a badge change should flash the taskbar entry (Windows): only the
// 0 → N transition, and only when the window isn't already in front.
function shouldFlashFrame(previousCount, nextCount, focused) {
  return previousCount === 0 && nextCount > 0 && !focused
}

// A tiny SVG badge (red disc + count) rendered as a data URL for
// `setOverlayIcon` on Windows, where there's no dock badge. Counts above 99
// collapse to "99+" so the glyph stays legible at 16px.
function badgeOverlaySvgDataUrl(count) {
  const label = count > 99 ? '99+' : String(count)
  const fontSize = label.length >= 3 ? 7 : label.length === 2 ? 9 : 11
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">` +
    `<circle cx="8" cy="8" r="8" fill="#d92d20"/>` +
    `<text x="8" y="8" fill="#ffffff" font-family="Segoe UI, Arial, sans-serif" font-size="${fontSize}" ` +
    `font-weight="700" text-anchor="middle" dominant-baseline="central">${label}</text>` +
    `</svg>`

  return `data:image/svg+xml;base64,${Buffer.from(svg, 'utf8').toString('base64')}`
}

module.exports = {
  badgeOverlaySvgDataUrl,
  buildNotificationOptions,
  normalizeBadgeCount,
  normalizeNotificationAction,
  shouldFlashFrame
}
