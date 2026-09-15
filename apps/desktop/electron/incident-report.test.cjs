const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const zlib = require('node:zlib')

const { autoReportEnabled, buildZip, countOccurrence, crc32, occurrencesSinceReport, recentlyReported, remember, reportIncident, statePath, uploadMinimalIncident } = require('./incident-report.cjs')

function mkHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-incident-test-'))
}

// Minimal ZIP reader for the test: walk the local headers and inflate each entry.
function readZip(buf) {
  const entries = {}
  let offset = 0
  while (buf.readUInt32LE(offset) === 0x04034b50) {
    const method = buf.readUInt16LE(offset + 8)
    const crc = buf.readUInt32LE(offset + 14)
    const compressedSize = buf.readUInt32LE(offset + 18)
    const nameLength = buf.readUInt16LE(offset + 26)
    const extraLength = buf.readUInt16LE(offset + 28)
    const name = buf.subarray(offset + 30, offset + 30 + nameLength).toString('utf8')
    const dataStart = offset + 30 + nameLength + extraLength
    const data = buf.subarray(dataStart, dataStart + compressedSize)
    const content = method === 8 ? zlib.inflateRawSync(data) : Buffer.from(data)
    assert.equal(crc32(content), crc, `crc of ${name}`)
    entries[name] = content.toString('utf8')
    offset = dataStart + compressedSize
  }
  assert.equal(buf.readUInt32LE(offset), 0x02014b50, 'central directory follows the entries')
  const eocd = buf.subarray(buf.length - 22)
  assert.equal(eocd.readUInt32LE(0), 0x06054b50)
  assert.equal(eocd.readUInt16LE(10), Object.keys(entries).length, 'entry count in the end record')
  return entries
}

test('crc32 matches the reference value', () => {
  assert.equal(crc32(Buffer.from('123456789')), 0xcbf43926)
})

test('buildZip produces a readable deflated archive', () => {
  const zip = buildZip({ 'metadata.json': '{"a":1}', 'incident-context.txt': 'kind: x\n' })
  const entries = readZip(zip)
  assert.deepEqual(entries, { 'metadata.json': '{"a":1}', 'incident-context.txt': 'kind: x\n' })
})

test('autoReportEnabled honours the environment gate', () => {
  assert.equal(autoReportEnabled({}), true)
  assert.equal(autoReportEnabled({ HERMES_SUPPORT_AUTO_REPORT: '0' }), false)
  assert.equal(autoReportEnabled({ HERMES_SUPPORT_AUTO_REPORT: 'off' }), false)
  assert.equal(autoReportEnabled({ HERMES_SUPPORT_AUTO_REPORT: '1' }), true)
})

test('rate limit state is shared with the CLI reporter format', () => {
  const home = mkHome()
  try {
    assert.equal(recentlyReported(home, 'k'), false)
    remember(home, 'k', 'SUP-1', 'summary')
    const state = JSON.parse(fs.readFileSync(statePath(home), 'utf8'))
    assert.equal(state.k.case_id, 'SUP-1')
    assert.ok(Math.abs(state.k.reported_at - Date.now() / 1000) < 5, 'epoch seconds like hermes_cli/incident_report.py')
    assert.equal(recentlyReported(home, 'k'), true)
    assert.equal(recentlyReported(home, 'k', Date.now() / 1000 + 25 * 3600), false)
    fs.writeFileSync(statePath(home), '{not json')
    assert.equal(recentlyReported(home, 'k'), false)
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

function startFakeSupportServer(received) {
  return new Promise(resolve => {
    const server = http.createServer((req, res) => {
      const chunks = []
      req.on('data', c => chunks.push(c))
      req.on('end', () => {
        const body = Buffer.concat(chunks)
        const marker = Buffer.from('Content-Type: application/zip\r\n\r\n')
        const start = body.indexOf(marker) + marker.length
        const boundary = req.headers['content-type'].split('boundary=')[1]
        const end = body.lastIndexOf(Buffer.from(`\r\n--${boundary}`))
        received.push({ headers: req.headers, path: req.url, zip: body.subarray(start, end), body: body.toString('latin1') })
        res.writeHead(202, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ support_case_id: 'SUP-TEST-1', job_id: 'job-1' }))
      })
    })
    server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/api/v1/upload` }))
  })
}

test('uploadMinimalIncident posts a schema-1.1.0 bundle as multipart/form-data', async () => {
  const received = []
  const { server, url } = await startFakeSupportServer(received)
  try {
    const result = await uploadMinimalIncident({ kind: 'installer-failure-venv', summary: 'venv failed', detail: 'uv missing', severity: 'high', contextType: 'install_failure', installType: 'fresh_install', clientVersion: '1.0.75', env: { SUPPORT_API_KEY: 'k-123' }, uploadUrl: url })
    assert.equal(result.ok, true)
    assert.equal(result.caseId, 'job-1')
    assert.equal(received.length, 1)
    const r = received[0]
    assert.equal(r.headers.authorization, 'Bearer k-123')
    assert.equal(r.headers['x-hermes-reason'], 'installer-failure-venv')
    assert.match(r.body, /name="support_case_id"\r\n\r\nSUP-\d{8}-\d{6}\r\n/)
    const entries = readZip(r.zip)
    const metadata = JSON.parse(entries['metadata.json'])
    assert.equal(metadata.schema_version, '1.1.0')
    assert.equal(metadata.issue_details.category, 'installation_update')
    assert.equal(metadata.issue_details.severity, 'high')
    assert.equal(metadata.context_type, 'install_failure')
    assert.equal(metadata.install_type, 'fresh_install')
    assert.equal(metadata.client_info.client_type, 'hermes-desktop')
    assert.equal(metadata.client_info.client_version, '1.0.75')
    assert.ok(entries['incident-context.txt'].includes('detail: uv missing'))
    assert.ok(JSON.parse(entries['manifest.json']).metadata.support_case_id.startsWith('SUP-'))
  } finally {
    server.close()
  }
})

test('reportIncident prefers the CLI bundle, falls back to the minimal upload, and rate-limits', async () => {
  const home = mkHome()
  const received = []
  const { server, url } = await startFakeSupportServer(received)
  const logs = []
  try {
    // CLI works → no minimal upload.
    const cliCalls = []
    const first = await reportIncident({ kind: 'update-check-fallback-git', summary: 'release repo down', detail: 'ENOTFOUND', contextType: 'update_failure', installType: 'update', hermesHome: home, uploadUrl: url, env: {}, log: l => logs.push(l), runCli: async payload => (cliCalls.push(payload), { ok: true, reference_id: 'SUP-CLI-1' }) })
    assert.deepEqual(first, { ok: true, caseId: 'SUP-CLI-1' })
    assert.equal(cliCalls.length, 1)
    assert.equal(cliCalls[0].reason, 'update-check-fallback-git')
    assert.equal(cliCalls[0].category, 'installation_update')
    assert.equal(cliCalls[0].contextType, 'update_failure')
    assert.equal(received.length, 0)

    // Same kind again → rate-limited.
    const again = await reportIncident({ kind: 'update-check-fallback-git', summary: 'x', hermesHome: home, uploadUrl: url, env: {}, log: l => logs.push(l), runCli: async () => ({ ok: true, reference_id: 'SUP-CLI-2' }) })
    assert.equal(again.skipped, 'rate-limited')
    assert.ok(logs.some(l => l.includes('already reported')))

    // CLI unavailable (no venv) → minimal upload.
    const second = await reportIncident({ kind: 'installer-failure-repository', summary: 'bootstrap failed', severity: 'high', contextType: 'install_failure', installType: 'fresh_install', hermesHome: home, uploadUrl: url, env: {}, log: l => logs.push(l), runCli: async () => ({ ok: false, error: 'hermes runtime unavailable (missing venv python)' }) })
    assert.equal(second.ok, true)
    assert.equal(received.length, 1)
    assert.equal(received[0].headers['x-hermes-reason'], 'installer-failure-repository')

    // Disabled → logged only.
    const off = await reportIncident({ kind: 'other', summary: 'x', hermesHome: home, uploadUrl: url, env: { HERMES_SUPPORT_AUTO_REPORT: '0' }, log: l => logs.push(l) })
    assert.equal(off.skipped, 'disabled')
    assert.equal(received.length, 1)
  } finally {
    server.close()
    fs.rmSync(home, { recursive: true, force: true })
  }
})

// AIS-344 (B2/B3): the minimal report ships the last boot section of
// desktop.log and the classified signal; rate-limited repeats are counted.
test('uploadMinimalIncident ships extra files and signals in the minimal bundle', async () => {
  const received = []
  const { server, url } = await startFakeSupportServer(received)
  try {
    const tail = '[hermes] [boot] Resolving Hermes backend\n[hermes] ERROR: [Errno 48] address already in use\n'
    const result = await uploadMinimalIncident({
      kind: 'boot-failure-port-in-use',
      summary: 'desktop boot failed after 5 attempt(s): Backend port already in use',
      detail: 'ERROR: [Errno 48] address already in use',
      severity: 'high',
      contextType: 'boot_error',
      installType: 'update',
      env: {},
      uploadUrl: url,
      extraFiles: { 'desktop-log-tail.txt': tail, 'ignored.txt': '   ', 'weird name!.txt': 'x' },
      signals: [{ id: 'boot.port_in_use', count: 1, severity: 'high', title: 'Backend port already in use' }]
    })
    assert.equal(result.ok, true)
    const entries = readZip(received[0].zip)
    assert.equal(entries['desktop-log-tail.txt'], tail)
    assert.equal(entries['weird_name_.txt'], 'x')
    assert.ok(!('ignored.txt' in entries), 'blank extras are dropped')
    const metadata = JSON.parse(entries['metadata.json'])
    assert.equal(metadata.context_type, 'boot_error')
    assert.deepEqual(metadata.issue_details.signals, [{ id: 'boot.port_in_use', count: 1, severity: 'high', title: 'Backend port already in use' }])
    assert.equal(metadata.issue_details.log_scope, 'minimal')
    assert.deepEqual(metadata.files.map(f => f.path).sort(), ['desktop-log-tail.txt', 'incident-context.txt', 'weird_name_.txt'])
  } finally {
    server.close()
  }
})

test('reportIncident counts rate-limited repeats and carries the count into the next report', async () => {
  const home = mkHome()
  const received = []
  const { server, url } = await startFakeSupportServer(received)
  try {
    const cliCalls = []
    const runCli = async payload => (cliCalls.push(payload), { ok: true, reference_id: 'SUP-CLI-9' })
    const base = { kind: 'boot-failure-port-in-use', summary: 'boot failed', contextType: 'boot_error', hermesHome: home, uploadUrl: url, env: {}, runCli }
    assert.equal((await reportIncident(base)).ok, true)
    assert.equal(occurrencesSinceReport(home, 'boot-failure-port-in-use'), 0)
    const second = await reportIncident(base)
    const third = await reportIncident(base)
    assert.equal(second.skipped, 'rate-limited')
    assert.equal(third.occurrences, 2)
    assert.equal(occurrencesSinceReport(home, 'boot-failure-port-in-use'), 2)
    assert.equal(cliCalls.length, 1)
    // Window expired → the next report mentions the repeats and resets the count.
    const statePath_ = statePath(home)
    const state = JSON.parse(fs.readFileSync(statePath_, 'utf8'))
    state['boot-failure-port-in-use'].reported_at -= 25 * 60 * 60
    fs.writeFileSync(statePath_, JSON.stringify(state))
    assert.equal((await reportIncident({ ...base, detail: 'EADDRINUSE' })).ok, true)
    assert.equal(cliCalls.length, 2)
    assert.match(cliCalls[1].userDescription, /EADDRINUSE\nseen 2 more time\(s\) since the last report/)
    assert.equal(occurrencesSinceReport(home, 'boot-failure-port-in-use'), 0)
    assert.equal(countOccurrence(home, 'never-seen'), 0)
  } finally {
    server.close()
    fs.rmSync(home, { recursive: true, force: true })
  }
})
