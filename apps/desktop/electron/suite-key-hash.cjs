'use strict'

// Suite key hash prefixes for the client telemetry (AIS-756).
//
// Mirrors hermes_cli/iamds_suite.py:suite_key_hash_prefixes. LiteLLM stores a
// virtual key as sha256(key) hex ("Key Hash (Token)"); the first 12 hex chars
// let support match a Suite event group (401 KeyNotFound …) to the client that
// sends the key — the key itself never leaves the machine.

const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const SUITE_KEY_ENVS = {
  prod: 'IAMDS_LITELLM_API_KEY',
  staging: 'IAMDS_LITELLM_STAGING_API_KEY',
  dev: 'IAMDS_LITELLM_DEV_API_KEY',
  localdev: 'IAMDS_LITELLM_LOCALDEV_API_KEY'
}
const PREFIX_LENGTH = 12

function parseEnvFile(text) {
  const values = {}
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match) continue
    let value = match[2].trim()
    if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value.endsWith(value[0])) {
      value = value.slice(1, -1)
    }
    values[match[1]] = value
  }
  return values
}

function usableKey(value) {
  const key = String(value || '').trim()
  return key.length >= 8 && !key.startsWith('${') ? key : ''
}

function suiteKeyHashPrefixes({ hermesHome, env = process.env, fsImpl = fs } = {}) {
  let fileValues = {}
  try {
    fileValues = parseEnvFile(fsImpl.readFileSync(path.join(String(hermesHome || ''), '.env'), 'utf8'))
  } catch {
    fileValues = {}
  }
  const out = {}
  for (const [name, keyEnv] of Object.entries(SUITE_KEY_ENVS)) {
    const key = usableKey(fileValues[keyEnv]) || usableKey(env && env[keyEnv])
    if (key) out[name] = crypto.createHash('sha256').update(key, 'utf8').digest('hex').slice(0, PREFIX_LENGTH)
  }
  return out
}

module.exports = { parseEnvFile, suiteKeyHashPrefixes }
