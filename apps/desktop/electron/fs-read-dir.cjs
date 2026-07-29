'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { resolveDirectoryForIpc } = require('./hardening.cjs')

const FS_READDIR_STAT_CONCURRENCY = 16

// Always-hidden noise (covers non-git projects too; gitignore catches many of
// these, but the project tree should keep the same hygiene without one).
const FS_READDIR_HIDDEN = new Set([
  '.git',
  '.hg',
  '.svn',
  '.cache',
  '.next',
  '.turbo',
  '.venv',
  '__pycache__',
  'build',
  'dist',
  'node_modules',
  'target',
  'venv'
])

// Windows user directory system/hidden files, junctions, and metadata
const WIN32_SYSTEM_NAMES = new Set([
  'appdata',
  'application data',
  'cookies',
  'desktop.ini',
  'local settings',
  'my documents',
  'nethood',
  'ntuser.ini',
  'ntuser.pol',
  'printhood',
  'recent',
  'sendto',
  'start menu',
  'templates',
  'thumbs.db',
  '$recycle.bin',
  'system volume information'
])

// macOS user directory system/media/protected directories that trigger TCC prompts
const DARWIN_SYSTEM_NAMES = new Set([
  'music',
  'movies',
  'pictures',
  'library',
  '.trash'
])

function isDarwinSystemOrHidden(name) {
  if (!name) return false
  return DARWIN_SYSTEM_NAMES.has(name.toLowerCase())
}

function isWindowsSystemOrHidden(name) {
  if (!name) return false
  const lower = name.toLowerCase()
  if (WIN32_SYSTEM_NAMES.has(lower)) {
    return true
  }
  if (lower.startsWith('ntuser.') || lower.startsWith('ntuser.dat')) {
    return true
  }
  if (lower.startsWith('$')) {
    return true
  }
  return false
}

function direntIsDirectory(dirent) {
  return typeof dirent.isDirectory === 'function' && dirent.isDirectory()
}

function direntIsFile(dirent) {
  return typeof dirent.isFile === 'function' && dirent.isFile()
}

function direntIsSymbolicLink(dirent) {
  return typeof dirent.isSymbolicLink === 'function' && dirent.isSymbolicLink()
}

function shouldStatDirent(dirent) {
  if (direntIsDirectory(dirent)) return false

  return direntIsSymbolicLink(dirent) || !direntIsFile(dirent)
}

async function entryForDirent(dirent, resolved, fsImpl) {
  const fullPath = path.join(resolved, dirent.name)
  let isDirectory = direntIsDirectory(dirent)
  let mtimeMs = 0

  try {
    const stats = await fsImpl.promises.stat(fullPath)
    mtimeMs = stats.mtimeMs || 0
    if (!isDirectory) {
      isDirectory = stats.isDirectory()
    }
  } catch {
    if (!isDirectory && shouldStatDirent(dirent)) {
      isDirectory = false
    }
  }

  return { name: dirent.name, path: fullPath, isDirectory, mtimeMs }
}

async function mapWithStatConcurrency(items, mapper) {
  const results = new Array(items.length)
  let nextIndex = 0

  async function runWorker() {
    while (nextIndex < items.length) {
      const index = nextIndex
      nextIndex += 1
      results[index] = await mapper(items[index])
    }
  }

  const workerCount = Math.min(FS_READDIR_STAT_CONCURRENCY, items.length)
  const workers = Array.from({ length: workerCount }, () => runWorker())
  await Promise.all(workers)

  return results
}

async function readDirForIpc(dirPath, options = {}) {
  const fsImpl = options.fs || fs
  let resolved

  try {
    ;({ resolvedPath: resolved } = await resolveDirectoryForIpc(dirPath, {
      fs: fsImpl,
      purpose: 'Directory read'
    }))
  } catch (error) {
    return { entries: [], error: error?.code || 'read-error' }
  }

  try {
    const dirents = await fsImpl.promises.readdir(resolved, { withFileTypes: true })
    const visibleDirents = dirents.filter(dirent => {
      if (FS_READDIR_HIDDEN.has(dirent.name)) return false
      if (process.platform === 'win32' && isWindowsSystemOrHidden(dirent.name)) return false
      if (process.platform === 'darwin' && isDarwinSystemOrHidden(dirent.name)) return false
      return true
    })
    const entries = await mapWithStatConcurrency(visibleDirents, dirent =>
      entryForDirent(dirent, resolved, fsImpl)
    )

    entries.sort((a, b) => {
      if (a.isDirectory !== b.isDirectory) {
        return Number(b.isDirectory) - Number(a.isDirectory)
      }
      if (a.isDirectory) {
        return a.name.localeCompare(b.name)
      }
      return (b.mtimeMs || 0) - (a.mtimeMs || 0) || a.name.localeCompare(b.name)
    })

    const sanitizedEntries = entries.map(({ name, path, isDirectory }) => ({ name, path, isDirectory }))

    return { entries: sanitizedEntries }
  } catch (error) {
    return { entries: [], error: error?.code || 'read-error' }
  }
}

module.exports = {
  readDirForIpc
}
