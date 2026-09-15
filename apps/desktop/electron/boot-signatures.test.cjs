// AIS-344 (B3): the desktop classifies boot failures with a JS copy of the
// boot subset of hermes_cli/support_signatures.py — the ids are pinned here.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const { BOOT_SIGNATURE_IDS, classifyBootFailure, lastBootSection, lastSignatureLine, signatureSlug } = require('./boot-signatures.cjs')

test('every JS signature id exists in the Python catalog', () => {
  const pyPath = path.join(__dirname, '..', '..', '..', 'hermes_cli', 'support_signatures.py')
  const source = fs.readFileSync(pyPath, 'utf8')
  const pyIds = new Set([...source.matchAll(/Signature\(\s*"([a-z0-9_.]+)"/g)].map(m => m[1]))
  assert.ok(pyIds.size >= 10, `python catalog parsed (${pyIds.size})`)
  for (const id of BOOT_SIGNATURE_IDS) {
    assert.ok(pyIds.has(id), `${id} missing in hermes_cli/support_signatures.py`)
  }
})

test('classifyBootFailure picks the most specific signature', () => {
  assert.equal(classifyBootFailure('OSError: [Errno 48] address already in use\nHermes backend exited before it became ready (1).').id, 'boot.port_in_use')
  assert.equal(classifyBootFailure('Hermes backend exited before it became ready (1). Log: x').id, 'boot.backend_exited_before_ready')
  assert.equal(classifyBootFailure('Hermes backend did not become ready: timeout').id, 'boot.backend_not_ready')
  assert.equal(classifyBootFailure('Traceback (most recent call last):\n  File "x"').id, 'python.traceback')
  assert.equal(classifyBootFailure('ERROR something odd').id, 'generic.error')
  assert.deepEqual(classifyBootFailure(''), { id: 'boot.unknown', title: 'Desktop boot failed', slug: 'unknown' })
})

test('signature slugs make stable rate-limit kinds', () => {
  assert.equal(signatureSlug('boot.port_in_use'), 'port-in-use')
  assert.equal(signatureSlug('boot.backend_exited_before_ready'), 'backend-exited-before-ready')
  assert.equal(signatureSlug('python.traceback'), 'python-traceback')
  assert.equal(classifyBootFailure('EADDRINUSE').slug, 'port-in-use')
})

test('lastSignatureLine returns the last matching line, trimmed', () => {
  const text = 'first\nINFO: Started server process\n  ERROR:    [Errno 48] address already in use\nHermes backend exited (1)\n'
  assert.equal(lastSignatureLine(text), 'ERROR:    [Errno 48] address already in use')
  assert.equal(lastSignatureLine('plain\nlast'), 'last')
  assert.equal(lastSignatureLine(''), '')
})

test('lastBootSection returns the lines from the last boot marker, capped', () => {
  const lines = ['old', '[hermes] [boot] Resolving Hermes backend', 'a', 'b', '[hermes] [boot] Resolving Hermes backend', 'c', 'd']
  assert.deepEqual(lastBootSection(lines), ['[hermes] [boot] Resolving Hermes backend', 'c', 'd'])
  assert.deepEqual(lastBootSection(lines, 2), ['c', 'd'])
  assert.deepEqual(lastBootSection(['x', 'y']), ['x', 'y'], 'no marker → whole tail')
  assert.deepEqual(lastBootSection('p\nq'), ['p', 'q'], 'accepts a string')
})
