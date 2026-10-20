// Pure helpers for the macOS in-app update swap (AIS-476).
//
// The running client installs the next release with this code. The app is
// going to be renamed ("Hermes" → the AIMDS product name), so nothing here may
// look for the literal Hermes.app:
// - the rebuilt/prebuilt app is found by shape in release/mac{-arm64,}/;
// - an app that runs from the release dir was already replaced there by
//   `hermes update` / `hermes desktop --build-only` — it is only relaunched,
//   from its (possibly new) path;
// - an app copied elsewhere (e.g. /Applications) is installed next to the old
//   one under the new name, the old bundle removed, with a rollback.
const fs = require('node:fs')
const path = require('node:path')

const MAC_RELEASE_DIRS = ['mac-arm64', 'mac']

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory()
  } catch {
    return false
  }
}

// The first `*.app` bundle with a Contents/MacOS dir under release/mac-arm64
// or release/mac, or null.
function findBuiltMacApp(releaseDir) {
  for (const sub of MAC_RELEASE_DIRS) {
    const dir = path.join(releaseDir, sub)
    let entries = []
    try {
      entries = fs.readdirSync(dir).filter(name => name.endsWith('.app')).sort()
    } catch {
      continue
    }
    for (const name of entries) {
      const bundle = path.join(dir, name)
      if (isDir(path.join(bundle, 'Contents', 'MacOS'))) return bundle
    }
  }
  return null
}

function isInside(child, parent) {
  const rel = path.relative(parent, child)
  return rel === '' || (!!rel && !rel.startsWith('..') && !path.isAbsolute(rel))
}

// What the swapper does: copy `src` to `dst` (null = nothing to copy), remove
// `oldApp` once `dst` is in place, relaunch `dst`.
function planMacSwap({ rebuiltApp, targetApp, releaseDir }) {
  if (!rebuiltApp || !targetApp) return null
  if (releaseDir && isInside(targetApp, releaseDir)) {
    return { src: null, dst: rebuiltApp, oldApp: null }
  }
  const dst = path.join(path.dirname(targetApp), path.basename(rebuiltApp))
  return { src: rebuiltApp, dst, oldApp: dst === targetApp ? null : targetApp }
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`
}

// Detached bash swapper: wait for the app process to exit, install, clear
// quarantine, relaunch. A failed install keeps (or restores) the old app and
// relaunches that one instead.
function buildMacSwapScript({ pid, plan, ditto = '/usr/bin/ditto', xattr = '/usr/bin/xattr', open = '/usr/bin/open' }) {
  return `#!/bin/bash
set -u
APP_PID=${Number(pid) || 0}
SRC=${shellQuote(plan.src || '')}
DST=${shellQuote(plan.dst)}
OLD=${shellQuote(plan.oldApp || '')}
if [ "$APP_PID" -gt 0 ]; then
  for _ in $(seq 1 240); do
    kill -0 "$APP_PID" 2>/dev/null || break
    sleep 0.5
  done
fi
if [ -n "$SRC" ] && [ "$SRC" != "$DST" ]; then
  rm -rf "$DST.hermes-update-new" 2>/dev/null || true
  if ${ditto} "$SRC" "$DST.hermes-update-new"; then
    rm -rf "$DST.hermes-update-old" 2>/dev/null || true
    if [ -e "$DST" ]; then mv "$DST" "$DST.hermes-update-old" 2>/dev/null || rm -rf "$DST"; fi
    if mv "$DST.hermes-update-new" "$DST"; then
      rm -rf "$DST.hermes-update-old" 2>/dev/null || true
      if [ -n "$OLD" ] && [ "$OLD" != "$DST" ]; then rm -rf "$OLD"; fi
    else
      if [ -e "$DST.hermes-update-old" ]; then mv "$DST.hermes-update-old" "$DST"; fi
      rm -rf "$DST.hermes-update-new" 2>/dev/null || true
      if [ -n "$OLD" ] && [ -e "$OLD" ]; then DST="$OLD"; fi
    fi
  else
    rm -rf "$DST.hermes-update-new" 2>/dev/null || true
    if [ -n "$OLD" ] && [ -e "$OLD" ]; then DST="$OLD"; fi
  fi
fi
${xattr} -dr com.apple.quarantine "$DST" 2>/dev/null || true
${open} "$DST"
`
}

module.exports = { buildMacSwapScript, findBuiltMacApp, planMacSwap }
