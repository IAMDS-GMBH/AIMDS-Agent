// Pure helpers for native notifications + the unread badge (AIS-305). No
// Electron imports so `node --test` can exercise them directly; main.cjs wires
// the results into `Notification` / `app.setBadgeCount` / `setOverlayIcon`.

const zlib = require('node:zlib')

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
// `icon` is the app icon for platforms whose notification daemon shows none
// by itself (Linux libnotify, Windows toasts); macOS takes the bundle icon and
// would render an explicit one as a second, content image (AIS-798).
function buildNotificationOptions(payload, { defaultTitle = 'Hermes', icon = null } = {}) {
  const source = payload && typeof payload === 'object' ? payload : {}
  const title = trimmedString(source.title) || defaultTitle
  const body = typeof source.body === 'string' ? source.body : ''
  const action = normalizeNotificationAction(source.action)
  const options = {
    title,
    body,
    silent: Boolean(source.silent)
  }
  if (icon) options.icon = icon

  return { action, options }
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

// The Windows taskbar overlay badge (AIS-305), drawn as a PNG (AIS-798).
// It used to be an SVG data URL, but Electron's nativeImage only decodes PNG
// and JPEG: the SVG became an empty image and Windows never showed a badge.
// Pure JS (zlib from Node, no canvas, no font) so `node --test` covers it: a
// red anti-aliased disc with the count in a 3×5 pixel font, rendered at 32px
// for `scaleFactor: 2` (16px logical, crisp on HiDPI taskbars).
const BADGE_SIZE = 32
const BADGE_FILL = [0xd9, 0x2d, 0x20]
const BADGE_INK = [0xff, 0xff, 0xff]
const BADGE_GLYPHS = {
  0: ['111', '101', '101', '101', '111'],
  1: ['010', '110', '010', '010', '111'],
  2: ['111', '001', '111', '100', '111'],
  3: ['111', '001', '111', '001', '111'],
  4: ['101', '101', '111', '001', '001'],
  5: ['111', '100', '111', '001', '111'],
  6: ['111', '100', '111', '101', '111'],
  7: ['111', '001', '001', '001', '001'],
  8: ['111', '101', '111', '101', '111'],
  9: ['111', '101', '111', '001', '111'],
  '+': ['000', '010', '111', '010', '000']
}
// Pixel scale per label length, so one to three characters fit the disc.
const BADGE_SCALE = { 1: 4, 2: 3, 3: 2 }

// Counts above 99 collapse to "99+" so the glyph stays legible at 16px.
function badgeLabel(count) {
  return count > 99 ? '99+' : String(count)
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n += 1) {
    let c = n
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(buffer) {
  let crc = 0xffffffff
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, crc])
}

// RGBA pixels (size×size×4) → PNG file bytes.
function encodePng(pixels, size) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header[8] = 8 // bit depth
  header[9] = 6 // colour type: RGBA
  const rows = Buffer.alloc((size * 4 + 1) * size)
  for (let y = 0; y < size; y += 1) {
    // Filter byte 0 (none), then the row.
    pixels.copy(rows, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4)
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', header),
    pngChunk('IDAT', zlib.deflateSync(rows)),
    pngChunk('IEND', Buffer.alloc(0))
  ])
}

function badgeOverlayPng(count) {
  const size = BADGE_SIZE
  const pixels = Buffer.alloc(size * size * 4)
  const radius = size / 2
  const samples = 4

  // Disc, 4×4 supersampled for a smooth edge.
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let inside = 0
      for (let sy = 0; sy < samples; sy += 1) {
        for (let sx = 0; sx < samples; sx += 1) {
          const dx = x + (sx + 0.5) / samples - radius
          const dy = y + (sy + 0.5) / samples - radius
          if (dx * dx + dy * dy <= radius * radius) inside += 1
        }
      }
      const offset = (y * size + x) * 4
      pixels[offset] = BADGE_FILL[0]
      pixels[offset + 1] = BADGE_FILL[1]
      pixels[offset + 2] = BADGE_FILL[2]
      pixels[offset + 3] = Math.round((inside / (samples * samples)) * 255)
    }
  }

  // Count, centred, one pixel-font column of space between glyphs.
  const label = badgeLabel(count)
  const scale = BADGE_SCALE[label.length] ?? 2
  const glyphWidth = 3 * scale
  const glyphHeight = 5 * scale
  const width = label.length * glyphWidth + (label.length - 1) * scale
  const left = Math.round((size - width) / 2)
  const top = Math.round((size - glyphHeight) / 2)
  ;[...label].forEach((char, index) => {
    const glyph = BADGE_GLYPHS[char]
    if (!glyph) return
    const glyphLeft = left + index * (glyphWidth + scale)
    glyph.forEach((row, gy) => {
      ;[...row].forEach((bit, gx) => {
        if (bit !== '1') return
        for (let py = 0; py < scale; py += 1) {
          for (let px = 0; px < scale; px += 1) {
            const offset = ((top + gy * scale + py) * size + glyphLeft + gx * scale + px) * 4
            pixels[offset] = BADGE_INK[0]
            pixels[offset + 1] = BADGE_INK[1]
            pixels[offset + 2] = BADGE_INK[2]
            pixels[offset + 3] = 255
          }
        }
      })
    })
  })

  return encodePng(pixels, size)
}

module.exports = {
  BADGE_SIZE,
  badgeLabel,
  badgeOverlayPng,
  buildNotificationOptions,
  normalizeBadgeCount,
  normalizeNotificationAction,
  shouldFlashFrame
}
