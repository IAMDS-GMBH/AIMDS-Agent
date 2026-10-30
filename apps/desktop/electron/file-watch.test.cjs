const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { watchFileForChanges } = require('./file-watch.cjs')

// A fake fs: `watch` either throws or returns an emitter the test drives, and
// `watchFile` records the poll listener.
function fakeFs({ watchThrows = null } = {}) {
  const calls = { unwatchFile: [], watchFile: [] }
  const watcher = new EventEmitter()
  watcher.closed = false
  watcher.close = () => {
    watcher.closed = true
  }
  let watchCallback = null

  return {
    calls,
    emitDirEvent: filename => watchCallback('change', filename),
    impl: {
      unwatchFile: (file, listener) => calls.unwatchFile.push({ file, listener }),
      watch: (_dir, callback) => {
        if (watchThrows) throw watchThrows
        watchCallback = callback
        return watcher
      },
      watchFile: (file, options, listener) => calls.watchFile.push({ file, listener, options })
    },
    watcher
  }
}

const stat = (mtimeMs, size = 10, ino = 1) => ({ ino, mtimeMs, size })

test('watches the directory and reports only the target file', () => {
  const fake = fakeFs()
  let changes = 0
  const handle = watchFileForChanges('/docs/report.md', () => (changes += 1), { fsImpl: fake.impl })

  assert.equal(handle.mode, 'watch')
  fake.emitDirEvent('other.md')
  fake.emitDirEvent('report.md')
  // No filename (some platforms): may be ours, so report it.
  fake.emitDirEvent(null)
  assert.equal(changes, 2)

  handle.close()
  assert.equal(fake.watcher.closed, true)
})

test('falls back to stat polling when the OS gives no watcher (ENOSPC on Linux)', () => {
  const error = Object.assign(new Error('watch limit reached'), { code: 'ENOSPC' })
  const fake = fakeFs({ watchThrows: error })
  const fallbacks = []
  let changes = 0
  const handle = watchFileForChanges('/docs/report.md', () => (changes += 1), {
    fsImpl: fake.impl,
    onFallback: reason => fallbacks.push(reason.code),
    pollIntervalMs: 250
  })

  assert.equal(handle.mode, 'poll')
  assert.deepEqual(fallbacks, ['ENOSPC'])
  const [{ file, listener, options }] = fake.calls.watchFile
  assert.equal(file, '/docs/report.md')
  assert.equal(options.interval, 250)

  listener(stat(1), stat(1))
  assert.equal(changes, 0, 'an unchanged stat is not a change')
  listener(stat(2), stat(1))
  listener(stat(2, 11), stat(2, 10))
  listener(stat(2, 10, 9), stat(2, 10, 1))
  assert.equal(changes, 3)

  handle.close()
  assert.deepEqual(fake.calls.unwatchFile, [{ file: '/docs/report.md', listener }])
})

test('switches to polling when a live watcher errors, instead of crashing', () => {
  const fake = fakeFs()
  const handle = watchFileForChanges('/docs/report.md', () => {}, { fsImpl: fake.impl })

  fake.watcher.emit('error', Object.assign(new Error('gone'), { code: 'EMFILE' }))
  assert.equal(handle.mode, 'poll')
  assert.equal(fake.watcher.closed, true)
  // A second error does not start a second poller.
  fake.watcher.emit('error', new Error('again'))
  assert.equal(fake.calls.watchFile.length, 1)

  handle.close()
  assert.equal(fake.calls.unwatchFile.length, 1)
})

test('an error after close does not start polling', () => {
  const fake = fakeFs()
  const handle = watchFileForChanges('/docs/report.md', () => {}, { fsImpl: fake.impl })

  handle.close()
  fake.watcher.emit('error', new Error('late'))
  assert.equal(fake.calls.watchFile.length, 0)
})

test('sees a real write through the platform watcher', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-file-watch-'))
  const file = path.join(dir, 'preview.md')
  fs.writeFileSync(file, 'one')

  try {
    await new Promise((resolve, reject) => {
      let written = false
      const timeout = setTimeout(() => {
        handle.close()
        reject(new Error('no change event within 5s'))
      }, 5000)
      // macOS may still deliver the creation event; only the later write counts.
      const handle = watchFileForChanges(file, () => {
        if (!written) return
        clearTimeout(timeout)
        handle.close()
        resolve()
      })
      setTimeout(() => {
        written = true
        fs.writeFileSync(file, 'two')
      }, 50)
    })
  } finally {
    fs.rmSync(dir, { force: true, recursive: true })
  }
})
