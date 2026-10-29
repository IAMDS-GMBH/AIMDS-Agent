const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const test = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { parseEnvFile, suiteKeyHashPrefixes } = require('./suite-key-hash.cjs')

const PROD_KEY = 'sk-revoked-prod-key-0000051wA'
const STAGING_KEY = 'sk-staging-key-2222233yC'
const sha = key => crypto.createHash('sha256').update(key).digest('hex')

function homeWithEnv(text) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-suite-key-hash-'))
  fs.writeFileSync(path.join(home, '.env'), text)
  return home
}

test('prefixes are the first 12 hex chars of LiteLLM\'s sha256 key hash', () => {
  const home = homeWithEnv(`# comment\nIAMDS_LITELLM_API_KEY=${PROD_KEY}\nexport IAMDS_LITELLM_STAGING_API_KEY="${STAGING_KEY}"\nOTHER=x\n`)
  const prefixes = suiteKeyHashPrefixes({ hermesHome: home, env: {} })
  assert.deepEqual(prefixes, { prod: sha(PROD_KEY).slice(0, 12), staging: sha(STAGING_KEY).slice(0, 12) })
  const text = JSON.stringify(prefixes)
  assert.ok(!text.includes(PROD_KEY) && !text.includes(STAGING_KEY))
})

test('the process env fills in what the .env file lacks; no key, no entry', () => {
  const home = homeWithEnv('IAMDS_LITELLM_DEV_API_KEY=${UNSET}\n')
  assert.deepEqual(suiteKeyHashPrefixes({ hermesHome: home, env: { IAMDS_LITELLM_API_KEY: PROD_KEY } }), {
    prod: sha(PROD_KEY).slice(0, 12)
  })
  assert.deepEqual(suiteKeyHashPrefixes({ hermesHome: path.join(home, 'missing'), env: {} }), {})
})

test('parseEnvFile keeps values with = and strips matching quotes', () => {
  assert.deepEqual(parseEnvFile("A=b=c\nB='q'\n  # x\nbad line\n"), { A: 'b=c', B: 'q' })
})
