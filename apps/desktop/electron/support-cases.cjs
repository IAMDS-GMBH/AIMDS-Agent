'use strict'

// Reporter self-service on the support tool (AIS-399): edit or withdraw an own
// open case with the edit token the upload returned. Runs in the main process,
// not the renderer, because PATCH/DELETE with a custom header would need CORS.

const DEFAULT_SUPPORT_API_BASE = 'https://suite-support.iamds.com/api/v1'
const LOCAL_HOSTS = new Set(['127.0.0.1', '::1', '[::1]', 'localhost'])
const REQUEST_TIMEOUT_MS = 15_000

// The case endpoints live next to the upload endpoint the report went to.
// Only https (or http to a local dev instance) carries the edit token.
function supportApiBase(uploadUrl) {
  try {
    const url = new URL(String(uploadUrl || ''))
    const local = LOCAL_HOSTS.has(url.hostname.toLowerCase())

    if ((url.protocol === 'https:' || (url.protocol === 'http:' && local)) && url.pathname.endsWith('/api/v1/upload')) {
      return `${url.origin}${url.pathname.slice(0, -'/upload'.length)}`
    }
  } catch {
    // fall through to the default
  }

  return DEFAULT_SUPPORT_API_BASE
}

async function supportCaseRequest(method, payload = {}, fields = null, fetchImpl = fetch) {
  const caseId = String(payload?.caseId || '').trim()
  const editToken = String(payload?.editToken || '').trim()

  if (!caseId || !editToken) {
    return { ok: false, code: 0, error: 'missing case id or edit token' }
  }

  const body = fields
    ? Object.fromEntries(Object.entries(fields).filter(([, value]) => typeof value === 'string'))
    : undefined

  try {
    const res = await fetchImpl(`${supportApiBase(payload.uploadUrl)}/cases/${encodeURIComponent(caseId)}`, {
      body: body ? JSON.stringify(body) : undefined,
      headers: { 'Content-Type': 'application/json', 'X-Case-Edit-Token': editToken },
      method,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    })
    const data = await res.json().catch(() => ({}))

    if (!res.ok) {
      return { ok: false, code: res.status, error: data?.error || `HTTP ${res.status}` }
    }

    return { ok: true, code: res.status, status: data?.status || '' }
  } catch (err) {
    return { ok: false, code: 0, error: err?.message || String(err) }
  }
}

function editSupportCase(payload = {}, fetchImpl = fetch) {
  return supportCaseRequest(
    'PATCH',
    payload,
    {
      category: payload?.category,
      severity: payload?.severity,
      summary: payload?.summary,
      user_description: payload?.userDescription
    },
    fetchImpl
  )
}

function withdrawSupportCase(payload = {}, fetchImpl = fetch) {
  return supportCaseRequest('DELETE', payload, null, fetchImpl)
}

module.exports = { DEFAULT_SUPPORT_API_BASE, editSupportCase, supportApiBase, withdrawSupportCase }
