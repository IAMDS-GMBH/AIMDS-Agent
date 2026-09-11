'use strict'

/**
 * bootstrap-runner.cjs
 *
 * Drives apps/desktop's first-launch install of Hermes Agent by spawning
 * scripts/install.ps1 stage-by-stage and streaming progress events back to
 * the renderer.
 *
 * Wired from electron/main.cjs:
 *   const { runBootstrap } = require('./bootstrap-runner.cjs')
 *   const result = await runBootstrap({
 *     installStamp,        // INSTALL_STAMP from main.cjs (may be null in dev)
 *     activeRoot,          // ACTIVE_HERMES_ROOT
 *     sourceRepoRoot,      // SOURCE_REPO_ROOT (for dev install.ps1 lookup)
 *     hermesHome,          // HERMES_HOME
 *     logRoot,             // HERMES_HOME/logs
 *     emit: ev => {...}    // event sink (sender.send or similar)
 *   })
 *
 * Emits events with shape:
 *   { type: 'manifest',  stages: [{name, title, category, needs_user_input}, ...] }
 *   { type: 'stage',     name, state: 'running'|'succeeded'|'skipped'|'failed',
 *                        json?, durationMs?, error? }
 *   { type: 'log',       stage?, line, stream: 'stdout'|'stderr' } // raw line from install.ps1
 *   { type: 'complete',  marker: <written marker payload> }
 *   { type: 'failed',    stage?, error }     // bootstrap aborted
 *
 * Resolves with the same shape as the final 'complete' or 'failed' event so
 * callers can await either way.
 *
 * NOT implemented yet (deferred to Phase 1E / 1F):
 *   - User-facing retry / cancel from the renderer (event channels exist;
 *     no UI consumes them yet)
 */

const fs = require('node:fs')
const fsp = require('node:fs/promises')
const path = require('node:path')
const https = require('node:https')
const { spawn } = require('node:child_process')

const IS_WINDOWS = process.platform === 'win32'
const CANONICAL_SOUL_REL_PATH = path.join(
  'installer',
  'skills-hidden',
  'aimds-loadout',
  'identity',
  'SOUL.md'
)

function hiddenWindowsChildOptions(options = {}) {
  if (!IS_WINDOWS || Object.prototype.hasOwnProperty.call(options, 'windowsHide')) {
    return options
  }
  return { ...options, windowsHide: true }
}

const STAMP_COMMIT_RE = /^[0-9a-f]{7,40}$/i
// Release tag of a stamp written from `.hermes-release.json` (AIS-313): the
// install scripts then install exactly this release from the public release
// repository instead of cloning the (private) source repository.
const STAMP_TAG_RE = /^v\d+\.\d+\.\d+(?:-rc\.\d+)?$/
const RELEASE_REPO = 'IAMDS-GMBH/AIMDS-Agent-Releases'
const RELEASE_MANIFEST_ASSET = 'hermes-release.json'

// Stages flagged needs_user_input=true in the manifest are skipped by the
// runner (passed -NonInteractive to install.ps1, which the install script
// itself handles by emitting skipped=true frames). The renderer / 1E onboarding
// overlay takes over for those concerns (API keys, model, persona, gateway).
// We let install.ps1's own -NonInteractive logic drive this rather than
// filtering client-side -- single source of truth.

// ---------------------------------------------------------------------------
// install.ps1 source resolution
// ---------------------------------------------------------------------------

function installScriptName() {
  return process.platform === 'win32' ? 'install.ps1' : 'install.sh'
}

function installScriptKind() {
  return process.platform === 'win32' ? 'powershell' : 'posix'
}

function resolveLocalInstallScript(sourceRepoRoot) {
  if (!sourceRepoRoot) return null
  const candidate = path.join(sourceRepoRoot, 'scripts', installScriptName())
  try {
    fs.accessSync(candidate, fs.constants.R_OK)
    return candidate
  } catch {
    return null
  }
}

function bootstrapCacheDir(hermesHome) {
  return path.join(hermesHome, 'bootstrap-cache')
}

function canonicalSoulCandidates({ sourceRepoRoot, activeRoot, hermesHome }) {
  const candidates = []
  if (sourceRepoRoot) candidates.push(path.join(sourceRepoRoot, CANONICAL_SOUL_REL_PATH))
  if (activeRoot) candidates.push(path.join(activeRoot, CANONICAL_SOUL_REL_PATH))
  if (hermesHome) candidates.push(path.join(hermesHome, 'hermes-agent', CANONICAL_SOUL_REL_PATH))
  return candidates
}

function resolveCanonicalSoulSource({ sourceRepoRoot, activeRoot, hermesHome }) {
  for (const candidate of canonicalSoulCandidates({ sourceRepoRoot, activeRoot, hermesHome })) {
    try {
      if (fs.statSync(candidate).isFile()) return candidate
    } catch {
      void 0
    }
  }
  return null
}

function enforceCanonicalSoul({ sourceRepoRoot, activeRoot, hermesHome, emit }) {
  const source = resolveCanonicalSoulSource({ sourceRepoRoot, activeRoot, hermesHome })
  if (!source) {
    throw new Error(
      `Could not locate canonical SOUL source (${CANONICAL_SOUL_REL_PATH}) during reinstall bootstrap`
    )
  }
  const target = path.join(hermesHome, 'SOUL.md')
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.copyFileSync(source, target)
  emit({
    type: 'log',
    line: `[bootstrap] enforced canonical SOUL.md from ${source} -> ${target}`
  })
  return { source, target }
}

// The install.sh / install.ps1 that ships inside the already-installed agent
// checkout under ~/.hermes/hermes-agent. Used as a last-resort fallback when
// the pinned commit can't be fetched from GitHub (e.g. a locally-built desktop
// app stamped to an unpushed HEAD).
function installedAgentInstallScript(hermesHome) {
  if (!hermesHome) return null
  const candidate = path.join(hermesHome, 'hermes-agent', 'scripts', installScriptName())
  try {
    fs.accessSync(candidate, fs.constants.R_OK)
    return candidate
  } catch {
    return null
  }
}

function cachedScriptPath(hermesHome, ref) {
  const safe = String(ref).replace(/[^A-Za-z0-9._-]/g, '_')
  return path.join(bootstrapCacheDir(hermesHome), `install-${safe}.${process.platform === 'win32' ? 'ps1' : 'sh'}`)
}

// The ref a stamp pins the install scripts to: the release tag when the
// desktop was built from a release-archive install, else the commit.
function stampRef(installStamp) {
  if (!installStamp) return null
  if (installStamp.tag && STAMP_TAG_RE.test(installStamp.tag)) return installStamp.tag
  if (installStamp.commit && STAMP_COMMIT_RE.test(installStamp.commit)) return installStamp.commit
  return null
}

// GET `url` following redirects (GitHub release downloads redirect to the
// asset store) and resolve the whole body as a Buffer.
function httpsGetBuffer(url, { redirects = 5 } = {}) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { 'User-Agent': 'hermes-desktop/bootstrap' } }, res => {
      const status = res.statusCode || 0
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location) {
        res.resume()
        if (redirects <= 0) {
          reject(new Error(`Too many redirects fetching ${url}`))
          return
        }
        const next = new URL(res.headers.location, url).toString()
        httpsGetBuffer(next, { redirects: redirects - 1 }).then(resolve, reject)
        return
      }
      if (status !== 200) {
        res.resume()
        reject(new Error(`HTTP ${status} from ${url}`))
        return
      }
      const chunks = []
      res.on('data', chunk => chunks.push(chunk))
      res.on('end', () => resolve(Buffer.concat(chunks)))
      res.on('error', reject)
    })
    request.on('error', reject)
  })
}

// `hermes-release.json` of a release tag in the public release repository,
// validated the way hermes_cli/release_update.py validates it.
async function fetchReleaseManifest(tag) {
  const url = `https://github.com/${RELEASE_REPO}/releases/download/${tag}/${RELEASE_MANIFEST_ASSET}`
  let manifest
  try {
    manifest = JSON.parse((await httpsGetBuffer(url)).toString('utf8'))
  } catch (err) {
    throw new Error(`Failed to fetch the release manifest for ${tag}: ${err.message}`)
  }
  if (!manifest || manifest.format !== 'hermes-release-v1') {
    throw new Error(`Release manifest for ${tag} is not a hermes-release-v1 manifest`)
  }
  const sha256 = String(manifest.sha256 || '').toLowerCase()
  if (manifest.tag !== tag || !/^[0-9a-f]{64}$/.test(sha256)) {
    throw new Error(`Release manifest for ${tag} is inconsistent (tag ${manifest.tag}, sha256 ${manifest.sha256})`)
  }
  if (manifest.source_archive !== `hermes-source-${manifest.version}.zip`) {
    throw new Error(`Release manifest for ${tag} names an unexpected archive: ${manifest.source_archive}`)
  }
  return { ...manifest, sha256 }
}

// Extract one file out of a zip archive with the platform's own tooling
// (unzip on POSIX, .NET on Windows) — Node ships no zip reader and the
// install scripts need bash / PowerShell anyway.
function extractZipEntry(zipPath, entry, destPath) {
  const { execFile } = require('node:child_process')
  return new Promise((resolve, reject) => {
    if (process.platform === 'win32') {
      const script =
        'Add-Type -AssemblyName System.IO.Compression.FileSystem; ' +
        `$zip = [System.IO.Compression.ZipFile]::OpenRead('${zipPath.replace(/'/g, "''")}'); ` +
        `try { $e = $zip.GetEntry('${entry.replace(/'/g, "''")}'); if (-not $e) { throw 'entry not found: ${entry.replace(/'/g, "''")}' }; ` +
        `[System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, '${destPath.replace(/'/g, "''")}', $true) } finally { $zip.Dispose() }`
      execFile(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script],
        hiddenWindowsChildOptions({ maxBuffer: 64 * 1024 * 1024 }),
        (err, _stdout, stderr) => (err ? reject(new Error(`extracting ${entry}: ${stderr || err.message}`)) : resolve(destPath))
      )
      return
    }
    execFile('unzip', ['-p', zipPath, entry], { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(`extracting ${entry}: ${String(stderr || err.message).trim()}`))
        return
      }
      if (!stdout || stdout.length === 0) {
        reject(new Error(`extracting ${entry}: empty entry`))
        return
      }
      fs.writeFileSync(destPath, stdout)
      resolve(destPath)
    })
  })
}

// AIS-313: the install scripts travel inside `hermes-source-<version>.zip`
// of the public release repository (they are not separate assets and the
// source repository is private). Download the archive of the stamp's tag,
// verify it against `hermes-release.json` and pull `scripts/<name>` out.
async function downloadReleaseInstallScript(tag, destPath) {
  const scriptName = installScriptName()
  const manifest = await fetchReleaseManifest(tag)
  const archiveUrl = `https://github.com/${RELEASE_REPO}/releases/download/${tag}/${manifest.source_archive}`
  const archive = await httpsGetBuffer(archiveUrl)
  const actual = require('node:crypto').createHash('sha256').update(archive).digest('hex')
  if (actual !== manifest.sha256) {
    throw new Error(`Checksum mismatch for ${manifest.source_archive}: expected ${manifest.sha256}, got ${actual}`)
  }
  fs.mkdirSync(path.dirname(destPath), { recursive: true })
  const zipPath = `${destPath}.${process.pid}.zip`
  const tmpPath = `${destPath}.tmp`
  try {
    fs.writeFileSync(zipPath, archive)
    await extractZipEntry(zipPath, `hermes-agent-${manifest.version}/scripts/${scriptName}`, tmpPath)
    fs.renameSync(tmpPath, destPath)
    return destPath
  } finally {
    for (const leftover of [zipPath, tmpPath]) {
      try {
        fs.unlinkSync(leftover)
      } catch {
        void 0
      }
    }
  }
}

// Network resolution for a stamp: only release tags can be fetched — the
// source repository (raw.githubusercontent.com) is private since AIS-314.
function downloadInstallScript(installStamp, destPath) {
  const tag = installStamp && installStamp.tag
  if (tag && STAMP_TAG_RE.test(tag)) {
    return downloadReleaseInstallScript(tag, destPath)
  }
  return Promise.reject(
    new Error(
      `Cannot fetch ${installScriptName()} for commit ${String((installStamp && installStamp.commit) || '?').slice(0, 12)}: ` +
        'the install stamp carries no release tag and the source repository is private (AIS-313)'
    )
  )
}

async function resolveInstallScript({ installStamp, sourceRepoRoot, hermesHome, emit, _download = downloadInstallScript }) {
  // 1. Dev shortcut: prefer a local checkout's installer so we can iterate
  //    without pushing. SOURCE_REPO_ROOT comes from main.cjs (path.resolve
  //    of APP_ROOT/../..).
  const localScript = resolveLocalInstallScript(sourceRepoRoot)
  if (localScript) {
    emit({ type: 'log', line: `[bootstrap] using local ${installScriptName()} at ${localScript}` })
    return { path: localScript, source: 'local', kind: installScriptKind() }
  }

  // 2. Packaged path: the stamp names a release tag (release-archive install)
  //    or a commit (developer build); the tag is what the release repository
  //    can serve.
  const ref = stampRef(installStamp)
  if (!ref) {
    throw new Error(
      `Cannot resolve ${installScriptName()}: no SOURCE_REPO_ROOT and no install stamp. ` +
        'This packaged build was produced without a valid build-time stamp.'
    )
  }
  const shortRef = STAMP_COMMIT_RE.test(ref) ? ref.slice(0, 12) : ref
  const identity = { commit: installStamp.commit || null, tag: installStamp.tag || null, kind: installScriptKind() }

  const cached = cachedScriptPath(hermesHome, ref)
  try {
    await fsp.access(cached, fs.constants.R_OK)
    emit({ type: 'log', line: `[bootstrap] using cached ${installScriptName()} for ${shortRef}` })
    return { path: cached, source: 'cache', ...identity }
  } catch {
    // not cached; download
  }

  emit({ type: 'log', line: `[bootstrap] fetching ${installScriptName()} for ${shortRef} from the release repository` })
  try {
    await _download(installStamp, cached)
    emit({ type: 'log', line: `[bootstrap] saved to ${cached}` })
    return { path: cached, source: 'download', ...identity }
  } catch (err) {
    // Nothing to fetch (commit-only stamp of a locally-built desktop app, no
    // network, release repository unavailable): fall back to the installer
    // that ships inside the already-installed agent checkout so the
    // bootstrap can still run instead of dying with a fatal error.
    const installed = installedAgentInstallScript(hermesHome)
    if (installed) {
      emit({
        type: 'log',
        line:
          `[bootstrap] release fetch failed (${err.message}); ` +
          `falling back to installed agent ${installScriptName()} at ${installed}`
      })
      try {
        fs.mkdirSync(path.dirname(cached), { recursive: true })
        fs.copyFileSync(installed, cached)
        return { path: cached, source: 'installed-agent', ...identity }
      } catch {
        // Cache copy failed (read-only FS, etc.) -- use the source path directly.
        return { path: installed, source: 'installed-agent', ...identity }
      }
    }
    throw err
  }
}

// ---------------------------------------------------------------------------
// powershell wrapper
// ---------------------------------------------------------------------------

// Canonical PowerShell 5.1 location under a Windows root (%SystemRoot%).
function powershellUnderRoot(root) {
  return path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
}

// Resolve the PowerShell interpreter to spawn.
//
// Spawning bare 'powershell.exe' trusts PATH to contain
// %SystemRoot%\System32\WindowsPowerShell\v1.0. On machines whose PATH was
// trimmed, truncated, or stored as a non-expanding REG_SZ (so %SystemRoot%
// never expands), that lookup fails and the spawn dies with ENOENT before
// install.ps1 ever runs — the installer stalls at "0 of 0 steps". Resolve by
// absolute path first, then fall back to PATH (powershell 5.1, then pwsh 7),
// then a bare name as a last resort.
function resolveWindowsPowerShell() {
  for (const v of ['SystemRoot', 'windir']) {
    const root = process.env[v]
    if (root) {
      const candidate = powershellUnderRoot(root)
      try {
        if (fs.statSync(candidate).isFile()) return candidate
      } catch {
        void 0
      }
    }
  }
  const pathDirs = (process.env.PATH || process.env.Path || '').split(path.delimiter).filter(Boolean)
  for (const exe of ['powershell.exe', 'pwsh.exe']) {
    for (const dir of pathDirs) {
      const candidate = path.join(dir, exe)
      try {
        if (fs.statSync(candidate).isFile()) return candidate
      } catch {
        void 0
      }
    }
  }
  return 'powershell.exe'
}

function spawnPowerShell(scriptPath, args, { emit, stageName, abortSignal, hermesHome } = {}) {
  return new Promise((resolve, reject) => {
    const ps = process.platform === 'win32' ? resolveWindowsPowerShell() : 'pwsh'
    const fullArgs = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scriptPath, ...args]

    const child = spawn(ps, fullArgs, hiddenWindowsChildOptions({
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        // Pass HERMES_HOME through so install.ps1 respects the caller's
        // choice rather than re-computing the default.
        HERMES_HOME: hermesHome || process.env.HERMES_HOME || ''
      }
    }))

    let stdout = ''
    let stderr = ''
    let killed = false

    const onAbort = () => {
      killed = true
      try {
        child.kill('SIGTERM')
      } catch {
        void 0
      }
    }
    if (abortSignal) {
      if (abortSignal.aborted) {
        onAbort()
      } else {
        abortSignal.addEventListener('abort', onAbort, { once: true })
      }
    }

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')

    // Stream stdout line-by-line so the renderer sees progress in real time.
    let stdoutBuf = ''
    child.stdout.on('data', chunk => {
      stdout += chunk
      stdoutBuf += chunk
      let nl
      while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
        const line = stdoutBuf.slice(0, nl).replace(/\r$/, '')
        stdoutBuf = stdoutBuf.slice(nl + 1)
        if (line) emit && emit({ type: 'log', stage: stageName, line, stream: 'stdout' })
      }
    })

    let stderrBuf = ''
    child.stderr.on('data', chunk => {
      stderr += chunk
      stderrBuf += chunk
      let nl
      while ((nl = stderrBuf.indexOf('\n')) !== -1) {
        const line = stderrBuf.slice(0, nl).replace(/\r$/, '')
        stderrBuf = stderrBuf.slice(nl + 1)
        if (line) emit && emit({ type: 'log', stage: stageName, line, stream: 'stderr' })
      }
    })

    child.on('error', err => {
      if (abortSignal) abortSignal.removeEventListener('abort', onAbort)
      reject(err)
    })

    child.on('close', (code, signal) => {
      if (abortSignal) abortSignal.removeEventListener('abort', onAbort)
      // Flush any trailing bytes
      if (stdoutBuf) emit && emit({ type: 'log', stage: stageName, line: stdoutBuf, stream: 'stdout' })
      if (stderrBuf) emit && emit({ type: 'log', stage: stageName, line: stderrBuf, stream: 'stderr' })
      resolve({ stdout, stderr, code, signal, killed })
    })
  })
}

function spawnBash(scriptPath, args, { emit, stageName, abortSignal, hermesHome } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn('bash', [scriptPath, ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        HERMES_HOME: hermesHome || process.env.HERMES_HOME || ''
      }
    })

    let stdout = ''
    let stderr = ''
    let killed = false

    const onAbort = () => {
      killed = true
      try {
        child.kill('SIGTERM')
      } catch {
        void 0
      }
    }
    if (abortSignal) {
      if (abortSignal.aborted) {
        onAbort()
      } else {
        abortSignal.addEventListener('abort', onAbort, { once: true })
      }
    }

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')

    let stdoutBuf = ''
    child.stdout.on('data', chunk => {
      stdout += chunk
      stdoutBuf += chunk
      let nl
      while ((nl = stdoutBuf.indexOf('\n')) !== -1) {
        const line = stdoutBuf.slice(0, nl).replace(/\r$/, '')
        stdoutBuf = stdoutBuf.slice(nl + 1)
        if (line) emit && emit({ type: 'log', stage: stageName, line, stream: 'stdout' })
      }
    })

    let stderrBuf = ''
    child.stderr.on('data', chunk => {
      stderr += chunk
      stderrBuf += chunk
      let nl
      while ((nl = stderrBuf.indexOf('\n')) !== -1) {
        const line = stderrBuf.slice(0, nl).replace(/\r$/, '')
        stderrBuf = stderrBuf.slice(nl + 1)
        if (line) emit && emit({ type: 'log', stage: stageName, line, stream: 'stderr' })
      }
    })

    child.on('error', err => {
      if (abortSignal) abortSignal.removeEventListener('abort', onAbort)
      reject(err)
    })

    child.on('close', (code, signal) => {
      if (abortSignal) abortSignal.removeEventListener('abort', onAbort)
      if (stdoutBuf) emit && emit({ type: 'log', stage: stageName, line: stdoutBuf, stream: 'stdout' })
      if (stderrBuf) emit && emit({ type: 'log', stage: stageName, line: stderrBuf, stream: 'stderr' })
      resolve({ stdout, stderr, code, signal, killed })
    })
  })
}

// ---------------------------------------------------------------------------
// Manifest + stage dispatch
// ---------------------------------------------------------------------------

// Build the install.ps1 pin args from the install-stamp. A release stamp
// (AIS-313) pins the exact release tag, which install.ps1 installs from the
// public release repository; a developer stamp keeps -Commit / -Branch so the
// repository stage clones the exact SHA the app was built from.
function buildPinArgs(installStamp) {
  const args = []
  if (installStamp && installStamp.tag && STAMP_TAG_RE.test(installStamp.tag)) {
    args.push('-Tag', installStamp.tag)
    return args
  }
  if (installStamp && installStamp.commit) {
    args.push('-Commit', installStamp.commit)
  }
  if (installStamp && installStamp.branch) {
    args.push('-Branch', installStamp.branch)
  }
  return args
}

function buildPosixPinArgs({ installStamp, activeRoot, hermesHome }) {
  const args = ['--dir', activeRoot, '--hermes-home', hermesHome]
  if (installStamp && installStamp.tag && STAMP_TAG_RE.test(installStamp.tag)) {
    args.push('--tag', installStamp.tag)
    return args
  }
  if (installStamp && installStamp.branch) {
    args.push('--branch', installStamp.branch)
  }
  if (installStamp && installStamp.commit) {
    args.push('--commit', installStamp.commit)
  }
  return args
}

async function fetchManifest({ scriptPath, installerKind, emit, hermesHome, activeRoot, installStamp }) {
  const isPosix = installerKind === 'posix'
  const args = isPosix
    ? ['--manifest', ...buildPosixPinArgs({ installStamp, activeRoot, hermesHome })]
    : ['-Manifest', ...buildPinArgs(installStamp)]
  const result = await (isPosix ? spawnBash : spawnPowerShell)(scriptPath, args, {
    emit,
    stageName: '__manifest__',
    hermesHome
  })
  if (result.code !== 0) {
    throw new Error(
      `${isPosix ? 'install.sh --manifest' : 'install.ps1 -Manifest'} failed: exit ${result.code}\n${result.stderr || result.stdout}`
    )
  }
  // The manifest is the LAST JSON line on stdout (install.ps1 may print
  // banner / info lines first depending on Console.OutputEncoding effects).
  // Find the last line that parses as JSON with a `stages` field.
  const lines = result.stdout.split(/\r?\n/).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(lines[i])
      if (parsed && Array.isArray(parsed.stages)) {
        return parsed
      }
    } catch {
      void 0
    }
  }
  throw new Error(
    `${isPosix ? 'install.sh --manifest' : 'install.ps1 -Manifest'} produced no parseable JSON payload\n${result.stdout}`
  )
}

// Parse the JSON result frame from a stage run. The protocol guarantees
// exactly one JSON line per stage in -Json or -Stage mode (post #27224 fix
// for the double-emit bug we addressed in the install.ps1 PR).
function parseStageResult(stdout) {
  const lines = stdout.split(/\r?\n/).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(lines[i])
      if (parsed && typeof parsed.ok === 'boolean' && typeof parsed.stage === 'string') {
        return parsed
      }
    } catch {
      void 0
    }
  }
  return null
}

async function runStage({ scriptPath, installerKind, stage, emit, hermesHome, activeRoot, abortSignal, installStamp }) {
  const startedAt = Date.now()
  emit({ type: 'stage', name: stage.name, state: 'running' })

  const isPosix = installerKind === 'posix'
  const args = isPosix
    ? [
        '--stage',
        stage.name,
        '--non-interactive',
        '--json',
        ...buildPosixPinArgs({ installStamp, activeRoot, hermesHome })
      ]
    : ['-Stage', stage.name, '-NonInteractive', '-Json', ...buildPinArgs(installStamp)]
  const result = await (isPosix ? spawnBash : spawnPowerShell)(scriptPath, args, {
    emit,
    stageName: stage.name,
    abortSignal,
    hermesHome
  })

  const durationMs = Date.now() - startedAt

  if (result.killed) {
    const ev = { type: 'stage', name: stage.name, state: 'failed', durationMs, error: 'cancelled by user' }
    emit(ev)
    return ev
  }

  const json = parseStageResult(result.stdout)

  if (!json) {
    const ev = {
      type: 'stage',
      name: stage.name,
      state: 'failed',
      durationMs,
      error: `${isPosix ? 'install.sh --stage' : 'install.ps1 -Stage'} ${stage.name} produced no JSON result frame (exit=${result.code})`,
      json: null
    }
    emit(ev)
    return ev
  }

  if (json.ok && json.skipped) {
    const ev = { type: 'stage', name: stage.name, state: 'skipped', durationMs, json }
    emit(ev)
    return ev
  }
  if (json.ok) {
    const ev = { type: 'stage', name: stage.name, state: 'succeeded', durationMs, json }
    emit(ev)
    return ev
  }
  const ev = {
    type: 'stage',
    name: stage.name,
    state: 'failed',
    durationMs,
    json,
    error: json.reason || `exit code ${result.code}`
  }
  emit(ev)
  return ev
}

// ---------------------------------------------------------------------------
// Per-run log file
// ---------------------------------------------------------------------------

function openRunLog(logRoot) {
  fs.mkdirSync(logRoot, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  const logPath = path.join(logRoot, `bootstrap-${ts}.log`)
  const stream = fs.createWriteStream(logPath, { flags: 'a' })
  return { path: logPath, stream }
}

// ---------------------------------------------------------------------------
// Public entrypoint
// ---------------------------------------------------------------------------

async function runBootstrap(opts) {
  const {
    installStamp,
    activeRoot,
    sourceRepoRoot,
    hermesHome,
    logRoot,
    onEvent,
    abortSignal,
    writeMarker // callback to write the bootstrap-complete marker; main.cjs provides
  } = opts

  // Bail before spawning anything if the user already cancelled — otherwise an
  // already-aborted signal would still fetch the manifest (a spawn) before the
  // in-loop abort check fires.
  if (abortSignal && abortSignal.aborted) {
    if (typeof onEvent === 'function') {
      try {
        onEvent({ type: 'failed', error: 'bootstrap cancelled by user' })
      } catch {
        void 0
      }
    }
    return { ok: false, cancelled: true }
  }

  const runLog = openRunLog(logRoot || path.join(hermesHome, 'logs'))

  // Tee every event to the runLog AND the caller's onEvent. This gives us a
  // forensic trail per bootstrap run AND lets the renderer subscribe live.
  const emit = ev => {
    try {
      runLog.stream.write(JSON.stringify(ev) + '\n')
    } catch {
      void 0
    }
    try {
      if (typeof onEvent === 'function') onEvent(ev)
    } catch (err) {
      // Don't let a subscriber bug crash the bootstrap
      runLog.stream.write(`emit error: ${err && err.message}\n`)
    }
  }

  emit({
    type: 'log',
    line:
      `[bootstrap] starting at ${new Date().toISOString()}; ` +
      `activeRoot=${activeRoot}; ` +
      `stamp=${installStamp ? installStamp.commit.slice(0, 12) : '<none>'}; ` +
      `runLog=${runLog.path}`
  })

  try {
    // 1. Resolve the platform installer.
    const scriptInfo = await resolveInstallScript({ installStamp, sourceRepoRoot, hermesHome, emit })
    const installerKind = scriptInfo.kind || 'powershell'

    // 2. Fetch manifest
    const manifest = await fetchManifest({
      scriptPath: scriptInfo.path,
      installerKind,
      emit,
      hermesHome,
      activeRoot,
      installStamp
    })
    emit({
      type: 'manifest',
      stages: manifest.stages,
      protocolVersion: manifest.protocol_version || manifest.protocolVersion || null
    })

    // 3. Iterate stages in order. Stages flagged needs_user_input are still
    //    invoked -- install.ps1's own -NonInteractive handler in those stages
    //    emits skipped=true. We trust the protocol rather than filtering
    //    client-side.
    for (const stage of manifest.stages) {
      if (abortSignal && abortSignal.aborted) {
        emit({ type: 'failed', error: 'bootstrap cancelled by user' })
        return { ok: false, cancelled: true }
      }
      const ev = await runStage({
        scriptPath: scriptInfo.path,
        installerKind,
        stage,
        emit,
        hermesHome,
        activeRoot,
        abortSignal,
        installStamp
      })
      if (ev.state === 'failed') {
        emit({ type: 'failed', stage: stage.name, error: ev.error || 'stage failed' })
        return { ok: false, failedStage: stage.name, error: ev.error }
      }
    }

    // 4. Enforce canonical SOUL.md after install stages complete.
    enforceCanonicalSoul({ sourceRepoRoot, activeRoot, hermesHome, emit })

    // 5. Write the bootstrap-complete marker.
    const markerPayload = {
      pinnedCommit: installStamp ? installStamp.commit : null,
      pinnedBranch: installStamp ? installStamp.branch : null,
      pinnedTag: installStamp ? installStamp.tag || null : null
    }
    const marker = typeof writeMarker === 'function' ? writeMarker(markerPayload) : markerPayload
    emit({ type: 'complete', marker })
    return { ok: true, marker }
  } catch (err) {
    emit({ type: 'failed', error: err.message || String(err) })
    return { ok: false, error: err.message || String(err) }
  } finally {
    try {
      runLog.stream.end()
    } catch {
      void 0
    }
  }
}

module.exports = {
  runBootstrap,
  // Exposed for testability
  parseStageResult,
  enforceCanonicalSoul,
  resolveCanonicalSoulSource,
  resolveLocalInstallScript,
  resolveInstallScript,
  installedAgentInstallScript,
  cachedScriptPath,
  buildPinArgs,
  buildPosixPinArgs,
  downloadInstallScript,
  fetchReleaseManifest,
  stampRef
}
