const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')

const { previewPathCandidates } = require('./preview-path.cjs')

const home = path.resolve('/Users/me')
const vault = path.join(home, 'Documents', 'AIMDS-Suite-Vault')

test('a home-relative vault path is tried from the cwd up to the home directory', () => {
  const rel = 'Documents/AIMDS-Suite-Vault/projects/plan.md'

  assert.deepEqual(previewPathCandidates(rel, vault, home), [
    path.join(vault, rel),
    path.join(home, 'Documents', rel),
    path.join(home, rel)
  ])
})

test('a cwd-relative path keeps the cwd first', () => {
  assert.equal(previewPathCandidates('projects/plan.md', vault, home)[0], path.join(vault, 'projects/plan.md'))
})

test('a cwd outside home falls back to home once', () => {
  const tmp = path.resolve('/tmp/work')

  assert.deepEqual(previewPathCandidates('notes/a.md', tmp, home), [path.join(tmp, 'notes/a.md'), path.join(home, 'notes/a.md')])
})

test('rooted and explicit relative targets are not searched', () => {
  assert.deepEqual(previewPathCandidates('/tmp/a.md', vault, home), ['/tmp/a.md'])
  assert.deepEqual(previewPathCandidates('./a.md', vault, home), ['./a.md'])
  assert.deepEqual(previewPathCandidates('../a.md', vault, home), ['../a.md'])
  assert.deepEqual(previewPathCandidates('file:///tmp/a.md', vault, home), ['file:///tmp/a.md'])
})
