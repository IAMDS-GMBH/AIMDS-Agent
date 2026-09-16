const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { execFile } = require('node:child_process')

function run(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, (error, stdout, stderr) => {
      if (error) {
        reject(
          new Error(
            `${command} ${args.join(' ')} failed: ${stderr?.trim() || stdout?.trim() || error.message}`
          )
        )
        return
      }
      resolve({ stdout, stderr })
    })
  })
}

function inlineKeyLooksValid(value) {
  return value.includes('BEGIN PRIVATE KEY') && value.includes('END PRIVATE KEY')
}

function resolveApiKeyPath(rawValue) {
  const value = String(rawValue || '').trim()
  if (!value) return { keyPath: '', cleanup: () => {} }

  if (fs.existsSync(value)) {
    return { keyPath: value, cleanup: () => {} }
  }

  if (!inlineKeyLooksValid(value)) {
    throw new Error('APPLE_API_KEY must be a file path or inline .p8 key content')
  }

  const tempPath = path.join(os.tmpdir(), `hermes-notary-${Date.now()}-${process.pid}.p8`)
  fs.writeFileSync(tempPath, value, 'utf8')
  return {
    keyPath: tempPath,
    cleanup: () => {
      try {
        fs.rmSync(tempPath, { force: true })
      } catch {
        // Best-effort cleanup.
      }
    }
  }
}

exports.default = async function notarize(context) {
  const { electronPlatformName, appOutDir, packager } = context
  if (electronPlatformName !== 'darwin') return

  const appName = packager.appInfo.productFilename
  const appPath = path.join(appOutDir, `${appName}.app`)
  if (!fs.existsSync(appPath)) {
    throw new Error(`Cannot notarize missing app bundle: ${appPath}`)
  }

  const profile = String(process.env.APPLE_NOTARY_PROFILE || '').trim()
  if (profile) {
    const zipPath = path.join(appOutDir, `${appName}.zip`)
    await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath])
    await run('xcrun', ['notarytool', 'submit', zipPath, '--keychain-profile', profile, '--wait'])
    await run('xcrun', ['stapler', 'staple', '-v', appPath])
    try {
      fs.rmSync(zipPath, { force: true })
    } catch {
      // Best-effort cleanup.
    }
    return
  }

  // AIS-353: the release pipeline holds an Apple ID + app-specific password +
  // team id (the Tauri installer's credentials) — notarize with those.
  const appleId = String(process.env.APPLE_ID || '').trim()
  const applePassword = String(process.env.APPLE_PASSWORD || '').trim()
  const teamId = String(process.env.APPLE_TEAM_ID || '').trim()
  if (appleId && applePassword && teamId) {
    const zipPath = path.join(appOutDir, `${appName}.zip`)
    try {
      await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath])
      await run('xcrun', [
        'notarytool', 'submit', zipPath,
        '--apple-id', appleId, '--password', applePassword, '--team-id', teamId,
        '--wait'
      ])
      await run('xcrun', ['stapler', 'staple', '-v', appPath])
    } finally {
      try {
        fs.rmSync(zipPath, { force: true })
      } catch {
        // Best-effort cleanup.
      }
    }
    return
  }

  const keyId = String(process.env.APPLE_API_KEY_ID || '').trim()
  const issuer = String(process.env.APPLE_API_ISSUER || '').trim()
  const rawApiKey = process.env.APPLE_API_KEY
  if (!rawApiKey || !keyId || !issuer) {
    if (process.env.HERMES_REQUIRE_NOTARIZATION === '1') {
      // A release build must never ship unnotarized by accident (AIS-353).
      throw new Error(
        'Notarization is required (HERMES_REQUIRE_NOTARIZATION=1) but no credentials are configured: ' +
          'set APPLE_ID + APPLE_PASSWORD + APPLE_TEAM_ID, or APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER, or APPLE_NOTARY_PROFILE.'
      )
    }
    console.log(
      'Skipping notarization: no Apple ID, App Store Connect API key or notary profile is configured.'
    )
    return
  }

  const { keyPath, cleanup } = resolveApiKeyPath(rawApiKey)
  const zipPath = path.join(appOutDir, `${appName}.zip`)
  try {
    await run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appPath, zipPath])
    await run('xcrun', ['notarytool', 'submit', zipPath, '--key', keyPath, '--key-id', keyId, '--issuer', issuer, '--wait'])
    await run('xcrun', ['stapler', 'staple', '-v', appPath])
  } finally {
    try {
      fs.rmSync(zipPath, { force: true })
    } catch {
      // Best-effort cleanup.
    }
    cleanup()
  }
}
