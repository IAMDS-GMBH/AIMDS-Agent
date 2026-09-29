'use strict'

// Install identity for telemetry and support cases (AIS-449).
//
// Mirrors hermes_cli/install_identity.py: one random id per install in
// `<hermes root>/.install-id`, shared by the desktop, the backend and the
// CLI. Telemetry used to key a client by `hostname-user`; on macOS the
// hostname follows the network, so one Mac showed up as several clients.

const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const INSTALL_ID_FILENAME = '.install-id'
const PREFIX = 'inst-'

function isValidInstallId(value) {
  return typeof value === 'string' && value.startsWith(PREFIX) && value.length > 8 && value.length <= 64 && /^[A-Za-z0-9-]+$/.test(value)
}

// A profile home (`<root>/profiles/<name>`) shares the root's id.
function installRoot(hermesHome) {
  const home = String(hermesHome || '')
  return path.basename(path.dirname(home)) === 'profiles' ? path.dirname(path.dirname(home)) : home
}

function installIdPath(hermesHome) {
  return path.join(installRoot(hermesHome), INSTALL_ID_FILENAME)
}

function readId(file, fsImpl) {
  try {
    const value = String(fsImpl.readFileSync(file, 'utf8')).trim()
    return isValidInstallId(value) ? value : ''
  } catch {
    return ''
  }
}

// The install id, created on first use; '' when it can be neither read nor written.
function getInstallId(hermesHome, { create = true, fsImpl = fs } = {}) {
  if (!hermesHome) return ''
  const file = installIdPath(hermesHome)
  const existing = readId(file, fsImpl)
  if (existing || !create) return existing
  const id = `${PREFIX}${crypto.randomUUID().replace(/-/g, '')}`
  try {
    fsImpl.mkdirSync(path.dirname(file), { recursive: true })
    // 'wx': when the backend wins the first-start race, keep its id.
    fsImpl.writeFileSync(file, `${id}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' })
    return id
  } catch (err) {
    if (err && err.code === 'EEXIST') return readId(file, fsImpl)
    return ''
  }
}

// The pre-AIS-449 id, sent once more so the support tool can merge the old row.
function legacyClientId({ hostname = os.hostname(), env = process.env } = {}) {
  return `${hostname || 'unknown-host'}-${env.USER || env.USERNAME || 'user'}`
}

module.exports = {
  INSTALL_ID_FILENAME,
  getInstallId,
  installIdPath,
  isValidInstallId,
  legacyClientId
}
