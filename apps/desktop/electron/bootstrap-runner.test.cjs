const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const {
  runBootstrap,
  enforceCanonicalSoul,
  resolveCanonicalSoulSource,
  resolveInstallScript,
  installedAgentInstallScript,
  cachedScriptPath
} = require('./bootstrap-runner.cjs')

const SCRIPT_NAME = process.platform === 'win32' ? 'install.ps1' : 'install.sh'

function mkTmpHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-bootstrap-test-'))
}

test('runBootstrap bails immediately when the signal is already aborted', async () => {
  const controller = new AbortController()
  controller.abort()

  const events = []
  const result = await runBootstrap({
    installStamp: null,
    activeRoot: '/tmp/hermes-runner-test',
    sourceRepoRoot: null,
    hermesHome: '/tmp/hermes-runner-test',
    logRoot: '/tmp/hermes-runner-test',
    onEvent: ev => events.push(ev),
    abortSignal: controller.signal
  })

  // Cancelled before any install script is spawned.
  assert.deepEqual(result, { ok: false, cancelled: true })
  assert.ok(
    events.some(ev => ev.type === 'failed' && /cancelled/i.test(ev.error)),
    'should emit a cancelled failure event'
  )
})

test('installedAgentInstallScript resolves the installer in the agent checkout', () => {
  const home = mkTmpHome()
  try {
    assert.equal(installedAgentInstallScript(home), null, 'absent before the checkout exists')

    const scriptsDir = path.join(home, 'hermes-agent', 'scripts')
    fs.mkdirSync(scriptsDir, { recursive: true })
    const scriptPath = path.join(scriptsDir, SCRIPT_NAME)
    fs.writeFileSync(scriptPath, '#!/bin/sh\necho hi\n')

    assert.equal(installedAgentInstallScript(home), scriptPath)
    assert.equal(installedAgentInstallScript(null), null, 'null home -> null')
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('resolveInstallScript prefers a cached script without touching the network', async () => {
  const home = mkTmpHome()
  try {
    const commit = 'a'.repeat(40)
    const cached = cachedScriptPath(home, commit)
    fs.mkdirSync(path.dirname(cached), { recursive: true })
    fs.writeFileSync(cached, '#!/bin/sh\necho cached\n')

    const logs = []
    const result = await resolveInstallScript({
      installStamp: { commit },
      sourceRepoRoot: null,
      hermesHome: home,
      emit: ev => logs.push(ev)
    })

    assert.equal(result.source, 'cache')
    assert.equal(result.path, cached)
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('resolveInstallScript falls back to the installed agent checkout on a 404', async () => {
  const home = mkTmpHome()
  try {
    const commit = 'a'.repeat(40)
    // Seed the installed agent checkout so the fallback has something to resolve.
    const scriptsDir = path.join(home, 'hermes-agent', 'scripts')
    fs.mkdirSync(scriptsDir, { recursive: true })
    const installed = path.join(scriptsDir, SCRIPT_NAME)
    fs.writeFileSync(installed, '#!/bin/sh\necho fallback\n')

    const logs = []
    const result = await resolveInstallScript({
      installStamp: { commit },
      sourceRepoRoot: null,
      hermesHome: home,
      emit: ev => logs.push(ev),
      // Simulate GitHub returning a 404 for the pinned commit.
      _download: async () => {
        throw new Error('Failed to download install.sh: HTTP 404')
      }
    })

    assert.equal(result.source, 'installed-agent')
    // It should have copied the installer into the bootstrap cache.
    assert.equal(result.path, cachedScriptPath(home, commit))
    assert.ok(fs.existsSync(result.path), 'fallback script copied into cache')
    assert.ok(
      logs.some(ev => /falling back to installed agent/.test(ev.line || '')),
      'emits a fallback log line'
    )
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('resolveInstallScript rethrows when the 404 fallback is unavailable', async () => {
  const home = mkTmpHome()
  try {
    const commit = 'a'.repeat(40)
    // No installed agent checkout seeded -> nothing to fall back to.
    await assert.rejects(
      resolveInstallScript({
        installStamp: { commit },
        sourceRepoRoot: null,
        hermesHome: home,
        emit: () => {},
        _download: async () => {
          throw new Error('Failed to download install.sh: HTTP 404')
        }
      }),
      /HTTP 404|Failed to download/
    )
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('resolveCanonicalSoulSource prefers sourceRepoRoot then activeRoot then installed agent', () => {
  const home = mkTmpHome()
  const src = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-source-'))
  const active = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-active-'))
  try {
    const rel = path.join('installer', 'skills-hidden', 'aimds-loadout', 'identity')
    const srcSoul = path.join(src, rel, 'SOUL.md')
    const activeSoul = path.join(active, rel, 'SOUL.md')
    const installedSoul = path.join(home, 'hermes-agent', rel, 'SOUL.md')
    fs.mkdirSync(path.dirname(activeSoul), { recursive: true })
    fs.mkdirSync(path.dirname(installedSoul), { recursive: true })
    fs.writeFileSync(activeSoul, 'active soul\n')
    fs.writeFileSync(installedSoul, 'installed soul\n')

    // sourceRepoRoot absent -> activeRoot wins
    assert.equal(
      resolveCanonicalSoulSource({ sourceRepoRoot: null, activeRoot: active, hermesHome: home }),
      activeSoul
    )

    // sourceRepoRoot present -> source wins
    fs.mkdirSync(path.dirname(srcSoul), { recursive: true })
    fs.writeFileSync(srcSoul, 'source soul\n')
    assert.equal(
      resolveCanonicalSoulSource({ sourceRepoRoot: src, activeRoot: active, hermesHome: home }),
      srcSoul
    )
  } finally {
    fs.rmSync(src, { recursive: true, force: true })
    fs.rmSync(active, { recursive: true, force: true })
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('enforceCanonicalSoul overwrites HERMES_HOME/SOUL.md from canonical source', () => {
  const home = mkTmpHome()
  const active = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-active-'))
  try {
    const rel = path.join('installer', 'skills-hidden', 'aimds-loadout', 'identity')
    const canonical = path.join(active, rel, 'SOUL.md')
    fs.mkdirSync(path.dirname(canonical), { recursive: true })
    fs.writeFileSync(canonical, '# SOUL canonical\n')

    const target = path.join(home, 'SOUL.md')
    fs.writeFileSync(target, '# old soul\n')
    const events = []
    const result = enforceCanonicalSoul({
      sourceRepoRoot: null,
      activeRoot: active,
      hermesHome: home,
      emit: ev => events.push(ev)
    })

    assert.equal(result.source, canonical)
    assert.equal(result.target, target)
    assert.equal(fs.readFileSync(target, 'utf8'), '# SOUL canonical\n')
    assert.ok(events.some(ev => ev.type === 'log' && /enforced canonical SOUL\.md/.test(ev.line || '')))
  } finally {
    fs.rmSync(active, { recursive: true, force: true })
    fs.rmSync(home, { recursive: true, force: true })
  }
})
