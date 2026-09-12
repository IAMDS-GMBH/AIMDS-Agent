'use strict'

// Automatic support cases for update / installer fallbacks (AIS-323).
//
// Mirrors hermes_cli/incident_report.py: the public release repository is the
// primary code source, the source repository the emergency fallback; every
// fallback and every failed bootstrap is logged and reported to the support
// server as a case — once per event kind per 24 h, never blocking, never
// throwing. The installed Hermes (`hermes support send-logs`, full redacted
// bundle) is preferred; when its venv is missing (a failed first install) a
// minimal bundle (metadata.json + context) is posted directly.

const fs = require('node:fs')
const https = require('node:https')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const zlib = require('node:zlib')

const DEFAULT_UPLOAD_URL = 'https://suite-support.iamds.com/api/v1/upload'
const STATE_FILENAME = 'incident-reports.json'
const WINDOW_SECONDS = 24 * 60 * 60
const CATEGORY = 'installation_update'

function autoReportEnabled(env = process.env) {
  const raw = String(env.HERMES_SUPPORT_AUTO_REPORT || '')
    .trim()
    .toLowerCase()
  if (['0', 'false', 'no', 'off'].includes(raw)) return false
  return true
}

function statePath(hermesHome) {
  return path.join(hermesHome, 'logs', STATE_FILENAME)
}

function readState(hermesHome) {
  try {
    const parsed = JSON.parse(fs.readFileSync(statePath(hermesHome), 'utf8'))
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeState(hermesHome, state) {
  const target = statePath(hermesHome)
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const tmp = `${target}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2) + '\n', 'utf8')
    fs.renameSync(tmp, target)
  } catch {
    // best-effort
  }
}

// Same schema as the Python reporter: {kind: {reported_at: epoch seconds, case_id, summary}}.
function recentlyReported(hermesHome, kind, now = Date.now() / 1000) {
  const entry = readState(hermesHome)[kind]
  if (!entry || typeof entry !== 'object') return false
  const last = Number(entry.reported_at)
  return Number.isFinite(last) && now - last < WINDOW_SECONDS
}

function remember(hermesHome, kind, caseId, summary) {
  const state = readState(hermesHome)
  state[kind] = { reported_at: Date.now() / 1000, case_id: caseId || '', summary: String(summary || '').slice(0, 200) }
  writeState(hermesHome, state)
}

// --- minimal stored ZIP (no zip library in Electron's main process) ----------

function crc32(buf) {
  let crc = ~0
  for (let i = 0; i < buf.length; i++) {
    crc ^= buf[i]
    for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1))
  }
  return ~crc >>> 0
}

function dosDateTime(date) {
  const time = ((date.getHours() & 0x1f) << 11) | ((date.getMinutes() & 0x3f) << 5) | ((date.getSeconds() >> 1) & 0x1f)
  const day = (((date.getFullYear() - 1980) & 0x7f) << 9) | (((date.getMonth() + 1) & 0x0f) << 5) | (date.getDate() & 0x1f)
  return { time, day }
}

// Build a ZIP archive with deflated entries: local headers + data, then the
// central directory. Enough for the support server's unpacker; no zip64.
function buildZip(files, date = new Date()) {
  const { time, day } = dosDateTime(date)
  const locals = []
  const centrals = []
  let offset = 0
  for (const [name, raw] of Object.entries(files)) {
    const nameBuf = Buffer.from(name, 'utf8')
    const data = Buffer.isBuffer(raw) ? raw : Buffer.from(String(raw), 'utf8')
    const deflated = zlib.deflateRawSync(data)
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4) // version needed
    local.writeUInt16LE(0x0800, 6) // utf-8 names
    local.writeUInt16LE(8, 8) // deflate
    local.writeUInt16LE(time, 10)
    local.writeUInt16LE(day, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(deflated.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBuf.length, 26)
    local.writeUInt16LE(0, 28)
    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(0x0800, 8)
    central.writeUInt16LE(8, 10)
    central.writeUInt16LE(time, 12)
    central.writeUInt16LE(day, 14)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(deflated.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(nameBuf.length, 28)
    central.writeUInt16LE(0, 30) // extra
    central.writeUInt16LE(0, 32) // comment
    central.writeUInt16LE(0, 34) // disk
    central.writeUInt16LE(0, 36) // internal attrs
    central.writeUInt32LE(0, 38) // external attrs
    central.writeUInt32LE(offset, 42)
    locals.push(local, nameBuf, deflated)
    centrals.push(central, nameBuf)
    offset += local.length + nameBuf.length + deflated.length
  }
  const centralBuf = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(0, 4)
  end.writeUInt16LE(0, 6)
  end.writeUInt16LE(centrals.length / 2, 8)
  end.writeUInt16LE(centrals.length / 2, 10)
  end.writeUInt32LE(centralBuf.length, 12)
  end.writeUInt32LE(offset, 16)
  end.writeUInt16LE(0, 20)
  return Buffer.concat([...locals, centralBuf, end])
}

function postMultipart(url, { headers, fields, file }) {
  return new Promise((resolve, reject) => {
    const boundary = `----hermes${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`
    const parts = []
    for (const [name, value] of Object.entries(fields)) {
      parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`))
    }
    parts.push(
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${file.name}"\r\nContent-Type: application/zip\r\n\r\n`
      ),
      file.data,
      Buffer.from(`\r\n--${boundary}--\r\n`)
    )
    const body = Buffer.concat(parts)
    const target = new URL(url)
    const transport = target.protocol === 'http:' ? http : https
    const req = transport.request(
      target,
      {
        method: 'POST',
        headers: { ...headers, 'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': body.length }
      },
      res => {
        const chunks = []
        res.on('data', c => chunks.push(c))
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8')
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(new Error(`HTTP ${res.statusCode} from ${url}: ${text.slice(0, 200)}`))
            return
          }
          let parsed = {}
          try {
            parsed = JSON.parse(text)
          } catch {
            parsed = {}
          }
          resolve(parsed)
        })
      }
    )
    req.setTimeout(25_000, () => req.destroy(new Error(`timeout posting to ${url}`)))
    req.on('error', reject)
    req.end(body)
  })
}

function caseIdNow(date = new Date()) {
  const p = n => String(n).padStart(2, '0')
  return `SUP-${date.getUTCFullYear()}${p(date.getUTCMonth() + 1)}${p(date.getUTCDate())}-${p(date.getUTCHours())}${p(date.getUTCMinutes())}${p(date.getUTCSeconds())}`
}

// Minimal bundle: metadata.json (schema 1.1.0), manifest.json, incident-context.txt.
async function uploadMinimalIncident({
  kind,
  summary,
  detail = '',
  severity = 'medium',
  contextType = 'update_failure',
  installType = 'update',
  clientType = 'hermes-desktop',
  clientVersion = '',
  env = process.env,
  uploadUrl = null
}) {
  const now = new Date()
  const caseId = caseIdNow(now)
  const context =
    `kind: ${kind}\nsummary: ${summary}\ndetail: ${detail}\nclient: ${clientType} ${clientVersion}\n` +
    `os: ${os.platform()} ${os.release()} (${os.arch()})\nnode: ${process.version}\ntimestamp: ${now.toISOString()}\n`
  const metadata = {
    schema_version: '1.1.0',
    support_case_id: caseId,
    customer_id: env.IAMDS_CUSTOMER_ID || 'cust-iamds',
    customer_name: env.IAMDS_CUSTOMER_NAME || 'IAMDS GmbH',
    litellm_url: env.IAMDS_LITELLM_BASE_URL || 'https://suite.iamds.com/litellm/v1',
    model_used: 'unknown',
    environment: env.HERMES_ENV || 'production',
    timestamp: now.toISOString(),
    client_info: { client_type: clientType, client_version: clientVersion || 'unknown', os: `${os.type()} ${os.release()} (${os.arch()})`, user_id: os.userInfo().username },
    issue_details: { category: CATEGORY, severity, summary: String(summary).slice(0, 200), user_description: detail },
    context_type: contextType,
    install_type: installType,
    lifecycle: { retention_days: 14, max_size_kb: 25600 },
    files: [{ path: 'incident-context.txt', mime_type: 'text/plain', size_bytes: Buffer.byteLength(context), content_category: 'log' }]
  }
  const manifest = { schema: 1, created_at: now.toISOString(), client: clientType, support_case_id: caseId, metadata }
  const zip = buildZip({ 'metadata.json': JSON.stringify(metadata, null, 2), 'manifest.json': JSON.stringify(manifest, null, 2), 'incident-context.txt': context })
  const url = uploadUrl || env.SUPPORT_UPLOAD_URL || DEFAULT_UPLOAD_URL
  const key = env.SUPPORT_API_KEY || 'anonymous'
  const response = await postMultipart(url, {
    headers: { Authorization: `Bearer ${key}`, 'User-Agent': 'hermes-desktop-incident/1', 'X-Hermes-Reason': kind, 'X-Hermes-Filename': `${caseId}.zip` },
    fields: { support_case_id: caseId },
    file: { name: `${caseId}.zip`, data: zip }
  })
  // Same precedence as hermes_cli/support_logs.py: the server's job id is the reference.
  return { ok: true, caseId: String(response.job_id || response.support_case_id || response.reference_id || caseId) }
}

// reportIncident: log + gate + rate limit + CLI-first upload + minimal fallback.
// `runCli(payload)` is main.cjs' runSupportLogUpload (full bundle through the
// installed Hermes); it may be absent or fail when the venv does not exist.
async function reportIncident({ kind, summary, detail = '', severity = 'medium', contextType, installType, clientType = 'hermes-desktop', clientVersion = '', hermesHome, runCli = null, log = () => {}, env = process.env, uploadUrl = null }) {
  const slug = String(kind || 'incident')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
  log(`[incident] ${slug}: ${summary}${detail ? ` — ${detail}` : ''}`)
  if (!autoReportEnabled(env)) {
    log(`[incident] ${slug} not reported (HERMES_SUPPORT_AUTO_REPORT is off)`)
    return { ok: false, skipped: 'disabled' }
  }
  if (hermesHome && recentlyReported(hermesHome, slug)) {
    log(`[incident] ${slug} already reported within the last 24 h — not reported again`)
    return { ok: false, skipped: 'rate-limited' }
  }
  let caseId = ''
  if (typeof runCli === 'function') {
    try {
      const result = await runCli({ reason: slug, category: CATEGORY, severity, summary: String(summary).slice(0, 200), userDescription: detail, clientType, clientVersion, contextType, installType })
      if (result && result.ok) {
        caseId = String(result.reference_id || result.referenceId || result.support_case_id || '')
      } else {
        log(`[incident] full bundle upload unavailable (${(result && result.error) || 'unknown'}); sending a minimal report`)
      }
    } catch (err) {
      log(`[incident] full bundle upload failed (${err.message}); sending a minimal report`)
    }
  }
  if (!caseId) {
    try {
      const minimal = await uploadMinimalIncident({ kind: slug, summary, detail, severity, contextType, installType, clientType, clientVersion, env, uploadUrl })
      caseId = minimal.caseId
    } catch (err) {
      log(`[incident] ${slug} could not be reported to support: ${err.message}`)
      return { ok: false, error: err.message }
    }
  }
  if (hermesHome) remember(hermesHome, slug, caseId, summary)
  log(`[incident] ${slug} reported to support as ${caseId || '(no id)'}`)
  return { ok: true, caseId }
}

module.exports = {
  CATEGORY,
  DEFAULT_UPLOAD_URL,
  STATE_FILENAME,
  autoReportEnabled,
  buildZip,
  crc32,
  recentlyReported,
  remember,
  reportIncident,
  statePath,
  uploadMinimalIncident
}
