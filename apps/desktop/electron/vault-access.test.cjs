// AIS-352: the main process asks for Documents access before the backend
// spawns, so the macOS dialog outlives any backend restart.
const test = require('node:test')
const assert = require('node:assert/strict')

const { ensureVaultAccess, permissionGuidance } = require('./vault-access.cjs')

function fakeFsp({ delayMs = 0, error = null, missingDir = false } = {}) {
  const calls = []
  return {
    calls,
    readdir(dir) {
      calls.push(dir)
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          if (missingDir && calls.length === 1) {
            const err = new Error('ENOENT')
            err.code = 'ENOENT'
            reject(err)
            return
          }
          if (error) {
            const err = new Error(error)
            err.code = error
            reject(err)
            return
          }
          resolve([])
        }, delayMs)
      })
    }
  }
}

test('non-macOS platforms and a missing dir are a no-op', async () => {
  const fsp = fakeFsp()
  const linux = await ensureVaultAccess({ dir: '/home/x/Documents/Vault', platform: 'linux', fsp })
  assert.equal(linux.skipped, true)
  assert.equal(fsp.calls.length, 0)
  const none = await ensureVaultAccess({ dir: '', platform: 'darwin', fsp })
  assert.equal(none.skipped, true)
})

test('a fast grant probes the dir once and never fires onSlow', async () => {
  const fsp = fakeFsp({ delayMs: 1 })
  let slowCalls = 0
  const result = await ensureVaultAccess({ dir: '/Users/x/Documents/Vault', platform: 'darwin', fsp, slowMs: 200, onSlow: () => (slowCalls += 1) })
  assert.equal(result.ok, true)
  assert.equal(result.slow, false)
  assert.equal(slowCalls, 0)
  assert.deepEqual(fsp.calls, ['/Users/x/Documents/Vault'])
})

test('a probe that blocks on the dialog fires onSlow once and still resolves ok', async () => {
  const fsp = fakeFsp({ delayMs: 60 })
  const logs = []
  let slowCalls = 0
  const result = await ensureVaultAccess({
    dir: '/Users/x/Documents/Vault',
    platform: 'darwin',
    fsp,
    slowMs: 10,
    onSlow: () => (slowCalls += 1),
    log: line => logs.push(line)
  })
  assert.equal(result.ok, true)
  assert.equal(result.slow, true)
  assert.equal(slowCalls, 1)
  assert.ok(result.waited_ms >= 50)
  assert.ok(logs.some(l => l.includes('permission dialog')))
  assert.ok(logs.some(l => l.includes('granted after')))
})

test('EPERM/EACCES is a denial with System Settings guidance', async () => {
  for (const code of ['EPERM', 'EACCES']) {
    const result = await ensureVaultAccess({ dir: '/Users/x/Documents/Vault', platform: 'darwin', fsp: fakeFsp({ error: code }) })
    assert.equal(result.ok, false)
    assert.equal(result.code, code)
    assert.match(result.message, /System Settings/)
    assert.equal(result.message, permissionGuidance('/Users/x/Documents/Vault'))
  }
})

test('a Vault that does not exist yet probes its parent (Documents) instead', async () => {
  const fsp = fakeFsp({ missingDir: true })
  const result = await ensureVaultAccess({ dir: '/Users/x/Documents/Vault', platform: 'darwin', fsp })
  assert.equal(result.ok, true)
  assert.deepEqual(fsp.calls, ['/Users/x/Documents/Vault', '/Users/x/Documents'])
})

test('other errors are reported but do not block the boot', async () => {
  const result = await ensureVaultAccess({ dir: '/Users/x/Documents/Vault', platform: 'darwin', fsp: fakeFsp({ error: 'EIO' }) })
  assert.equal(result.ok, true)
  assert.equal(result.error, 'EIO')
})
