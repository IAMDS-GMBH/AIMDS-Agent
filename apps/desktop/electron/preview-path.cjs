const path = require('node:path')

// Where a preview target may live, in the order to try (AIS-466).
//
// Rooted targets (`/x`, `~/x` already expanded, `./x`, `../x`, `C:\x`, file:)
// resolve against `baseDir` only. A plain relative path
// (`Documents/Vault/projects/plan.md`) is what the assistant writes for vault
// files relative to the home directory while the session cwd is the vault
// itself, so it is tried against `baseDir`, then each parent of `baseDir` up to
// `homeDir`, then `homeDir`. The caller takes the first candidate that exists.
function previewPathCandidates(raw, baseDir, homeDir) {
  const value = String(raw || '').trim()
  const base = path.resolve(baseDir)

  if (!value || /^file:/i.test(value) || path.isAbsolute(value) || /^\.\.?[\\/]/.test(value)) {
    return [value]
  }

  const home = homeDir ? path.resolve(homeDir) : ''
  const dirs = [base]
  const insideHome = dir => home && (dir === home || isWithin(home, dir))

  if (insideHome(base)) {
    let dir = base

    while (dir !== home) {
      dir = path.dirname(dir)
      dirs.push(dir)
    }
  } else if (home) {
    dirs.push(home)
  }

  return [...new Set(dirs.map(dir => path.join(dir, value)))]
}

function isWithin(root, dir) {
  const rel = path.relative(root, dir)
  return Boolean(rel) && !rel.startsWith('..') && !path.isAbsolute(rel)
}

module.exports = { previewPathCandidates }
