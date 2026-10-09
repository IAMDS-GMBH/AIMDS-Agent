import type { DesktopUpdateStatus, DesktopVersionInfo } from '@/global'

/**
 * How far behind an install is (AIS-527). Four stable clients sat on
 * 0.7.6-rc.11 / 0.7.8-rc.1 for weeks: the update toast said "1 new change"
 * and snoozed for a day on every close. Versions say what is at stake.
 */

const VERSION_RE = /^v?(\d+)\.(\d+)\.(\d+)/

/** Releases between two versions: patch steps within a minor, 99 across
 *  minors/majors, 0 when either side is unknown or not behind. */
export function releaseGap(current?: string | null, target?: string | null): number {
  const a = VERSION_RE.exec(String(current ?? '').trim())
  const b = VERSION_RE.exec(String(target ?? '').trim())

  if (!a || !b) {
    return 0
  }

  const [ma, mi, pa] = a.slice(1).map(Number)
  const [mb, mj, pb] = b.slice(1).map(Number)

  if (mb > ma || (mb === ma && mj > mi)) {
    return 99
  }

  if (mb === ma && mj === mi && pb > pa) {
    return pb - pa
  }

  return 0
}

/** The installed version as the user knows it (`v0.7.6-rc.11`). */
export function currentVersionOf(
  status: DesktopUpdateStatus | null | undefined,
  version?: DesktopVersionInfo | null
): string {
  const raw = status?.releaseVersion || status?.headTag || version?.releaseTag || version?.appVersion || ''

  if (!raw) {
    return ''
  }

  return raw.startsWith('v') ? raw : `v${raw}`
}

export function targetVersionOf(status: DesktopUpdateStatus | null | undefined): string {
  const raw = status?.targetTag || status?.targetVersion || ''

  if (!raw) {
    return ''
  }

  return raw.startsWith('v') ? raw : `v${raw}`
}

export const OUTDATED_RELEASE_GAP = 2
export const OUTDATED_AFTER_MS = 7 * 24 * 60 * 60 * 1000

/** Far enough behind that the notice must not be snoozed any more: two or
 *  more releases, or an update that has been waiting for a week. */
export function isOutdated(
  current: string,
  target: string,
  availableSince: number | null,
  now: number = Date.now()
): boolean {
  if (releaseGap(current, target) >= OUTDATED_RELEASE_GAP) {
    return true
  }

  return availableSince !== null && now - availableSince >= OUTDATED_AFTER_MS
}
