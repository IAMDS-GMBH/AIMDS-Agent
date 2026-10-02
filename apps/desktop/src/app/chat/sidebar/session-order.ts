import type { SessionInfo } from '@/hermes'

export const sessionTime = (s: SessionInfo) => s.last_active || s.started_at || 0

export const baseName = (path: string) =>
  path
    .replace(/[/\\]+$/, '')
    .split(/[/\\]/)
    .filter(Boolean)
    .pop()

/** Newest activity first: a session that gets a new message moves to the top. */
export function sortByLatestActivity(sessions: SessionInfo[]): SessionInfo[] {
  return [...sessions].sort((a, b) => sessionTime(b) - sessionTime(a))
}

export interface WorkspaceSessionGroup {
  id: string
  label: string
  path: null | string
  sessions: SessionInfo[]
}

export function workspaceGroupsFor(
  sessions: SessionInfo[],
  noWorkspaceLabel: string,
  options: { preserveSessionOrder?: boolean } = {}
): WorkspaceSessionGroup[] {
  const groups = new Map<string, WorkspaceSessionGroup>()

  for (const session of sessions) {
    const path = session.cwd?.trim() || ''
    const id = path || '__no_workspace__'
    const label = baseName(path) || path || noWorkspaceLabel

    const group = groups.get(id) ?? { id, label, path: path || null, sessions: [] }
    group.sessions.push(session)
    groups.set(id, group)
  }

  if (!options.preserveSessionOrder) {
    // Groups keep recency order (Map insertion = first-seen in the recency-sorted
    // input, so an active project floats up), and rows within a group follow
    // the latest activity too (AIS-459).
    for (const group of groups.values()) {
      group.sessions.sort((a, b) => sessionTime(b) - sessionTime(a))
    }
  }

  return [...groups.values()]
}
