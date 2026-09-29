const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { getInstallId, installIdPath, isValidInstallId, legacyClientId } = require('./install-id.cjs')

function mkHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-install-id-test-'))
}

test('the install id is created once and reused', () => {
  const home = mkHome()
  const first = getInstallId(home)
  assert.ok(isValidInstallId(first))
  assert.equal(getInstallId(home), first)
  assert.equal(fs.readFileSync(path.join(home, '.install-id'), 'utf8').trim(), first)
})

test('an id the Python side wrote is kept', () => {
  const home = mkHome()
  fs.writeFileSync(path.join(home, '.install-id'), 'inst-0123456789abcdef0123456789abcdef\n')
  assert.equal(getInstallId(home), 'inst-0123456789abcdef0123456789abcdef')
})

test('garbage is not reported and create:false never writes', () => {
  const home = mkHome()
  assert.equal(getInstallId(home, { create: false }), '')
  assert.equal(fs.existsSync(path.join(home, '.install-id')), false)
  fs.writeFileSync(path.join(home, '.install-id'), 'not an id')
  assert.equal(getInstallId(home, { create: false }), '')
})

test('a profile home shares the root id', () => {
  assert.equal(installIdPath('/data/hermes/profiles/coder'), path.join('/data/hermes', '.install-id'))
  assert.equal(installIdPath('/Users/jane/.hermes'), path.join('/Users/jane/.hermes', '.install-id'))
})

test('losing the first-start race returns the winner id', () => {
  const winner = 'inst-feedfacefeedfacefeedfacefeedface'
  let reads = 0
  const fsImpl = {
    readFileSync: () => {
      reads += 1
      if (reads === 1) throw Object.assign(new Error('missing'), { code: 'ENOENT' })
      return `${winner}\n`
    },
    mkdirSync: () => {},
    writeFileSync: () => { throw Object.assign(new Error('exists'), { code: 'EEXIST' }) }
  }
  assert.equal(getInstallId('/x', { fsImpl }), winner)
})

test('legacy id is hostname-user', () => {
  assert.equal(legacyClientId({ hostname: 'MacBook-Pro.local', env: { USER: 'jane' } }), 'MacBook-Pro.local-jane')
})
