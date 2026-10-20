const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { buildMacSwapScript, findBuiltMacApp, planMacSwap } = require('./update-swap.cjs')

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-swap-'))
}

function makeBundle(dir, name, marker = name) {
  const macos = path.join(dir, name, 'Contents', 'MacOS')
  fs.mkdirSync(macos, { recursive: true })
  fs.writeFileSync(path.join(macos, 'marker'), marker)
  return path.join(dir, name)
}

// AIS-476: the next release ships the app under its new name; this code must find it.
test('findBuiltMacApp finds a renamed bundle in mac-arm64 or mac', () => {
  const release = tmpdir()
  assert.equal(findBuiltMacApp(release), null)
  const renamed = makeBundle(path.join(release, 'mac'), 'AIMDS Agent.app')
  assert.equal(findBuiltMacApp(release), renamed)
  const arm = makeBundle(path.join(release, 'mac-arm64'), 'Hermes.app')
  assert.equal(findBuiltMacApp(release), arm)
})

test('planMacSwap relaunches an app that runs from the release dir', () => {
  const release = '/Users/u/.hermes/hermes-agent/apps/desktop/release'
  const plan = planMacSwap({
    rebuiltApp: `${release}/mac-arm64/AIMDS Agent.app`,
    targetApp: `${release}/mac-arm64/Hermes.app`,
    releaseDir: release
  })
  assert.deepEqual(plan, { src: null, dst: `${release}/mac-arm64/AIMDS Agent.app`, oldApp: null })
})

test('planMacSwap installs a copied app next to the old one under the new name', () => {
  const plan = planMacSwap({
    rebuiltApp: '/r/release/mac-arm64/AIMDS Agent.app',
    targetApp: '/Applications/Hermes.app',
    releaseDir: '/r/release'
  })
  assert.deepEqual(plan, { src: '/r/release/mac-arm64/AIMDS Agent.app', dst: '/Applications/AIMDS Agent.app', oldApp: '/Applications/Hermes.app' })
  const same = planMacSwap({ rebuiltApp: '/r/release/mac/Hermes.app', targetApp: '/Applications/Hermes.app', releaseDir: '/r/release' })
  assert.equal(same.oldApp, null)
  assert.equal(planMacSwap({ rebuiltApp: null, targetApp: '/Applications/Hermes.app' }), null)
})

function runScript(plan, { ditto = '/bin/cp -R' } = {}) {
  const script = buildMacSwapScript({ pid: 0, plan, ditto, xattr: 'true', open: 'echo OPEN' })
  const file = path.join(tmpdir(), 'swap.sh')
  fs.writeFileSync(file, script, { mode: 0o755 })
  const result = spawnSync('/bin/bash', [file], { encoding: 'utf8' })
  return result.stdout.trim()
}

test('swap script installs the renamed bundle and removes the old one', () => {
  const apps = tmpdir()
  const old = makeBundle(apps, 'Hermes.app', 'old')
  const src = makeBundle(tmpdir(), 'AIMDS Agent.app', 'new')
  const plan = planMacSwap({ rebuiltApp: src, targetApp: old, releaseDir: '/nowhere' })
  const opened = runScript(plan)
  assert.equal(opened, `OPEN ${path.join(apps, 'AIMDS Agent.app')}`)
  assert.equal(fs.readFileSync(path.join(apps, 'AIMDS Agent.app', 'Contents', 'MacOS', 'marker'), 'utf8'), 'new')
  assert.equal(fs.existsSync(old), false)
  assert.deepEqual(fs.readdirSync(apps), ['AIMDS Agent.app'])
})

test('swap script keeps and relaunches the old app when the copy fails', () => {
  const apps = tmpdir()
  const old = makeBundle(apps, 'Hermes.app', 'old')
  const plan = { src: '/does/not/exist.app', dst: path.join(apps, 'AIMDS Agent.app'), oldApp: old }
  const opened = runScript(plan, { ditto: 'false' })
  assert.equal(opened, `OPEN ${old}`)
  assert.deepEqual(fs.readdirSync(apps), ['Hermes.app'])
})

test('swap script replaces a same-named bundle in place', () => {
  const apps = tmpdir()
  const old = makeBundle(apps, 'Hermes.app', 'old')
  const src = makeBundle(tmpdir(), 'Hermes.app', 'new')
  const opened = runScript(planMacSwap({ rebuiltApp: src, targetApp: old, releaseDir: '/nowhere' }))
  assert.equal(opened, `OPEN ${old}`)
  assert.equal(fs.readFileSync(path.join(old, 'Contents', 'MacOS', 'marker'), 'utf8'), 'new')
  assert.deepEqual(fs.readdirSync(apps), ['Hermes.app'])
})
