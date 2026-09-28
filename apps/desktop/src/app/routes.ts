export const SESSION_ROUTE_PREFIX = '/'
export const NEW_CHAT_ROUTE = '/'
export const SETTINGS_ROUTE = '/settings'
export const COMMAND_CENTER_ROUTE = '/command-center'
export const SKILLS_ROUTE = '/skills'
export const ARTIFACTS_ROUTE = '/artifacts'
export const TODOS_ROUTE = '/todos'
export const CRON_ROUTE = '/cron'
export const PROFILES_ROUTE = '/profiles'
export const AGENTS_ROUTE = '/agents'
export const HUB_ROUTE = '/hub'

export type AppView =
  | 'agents'
  | 'artifacts'
  | 'todos'
  | 'chat'
  | 'command-center'
  | 'cron'
  | 'hub'
  | 'profiles'
  | 'settings'
  | 'skills'

export type AppRouteId =
  | 'agents'
  | 'artifacts'
  | 'todos'
  | 'command-center'
  | 'cron'
  | 'hub'
  | 'new'
  | 'profiles'
  | 'settings'
  | 'skills'

export interface AppRoute {
  id: AppRouteId
  path: string
  view: AppView
}

export const APP_ROUTES = [
  { id: 'new', path: NEW_CHAT_ROUTE, view: 'chat' },
  { id: 'settings', path: SETTINGS_ROUTE, view: 'settings' },
  { id: 'command-center', path: COMMAND_CENTER_ROUTE, view: 'command-center' },
  { id: 'skills', path: SKILLS_ROUTE, view: 'skills' },
  { id: 'hub', path: HUB_ROUTE, view: 'hub' },
  { id: 'artifacts', path: ARTIFACTS_ROUTE, view: 'artifacts' },
  { id: 'todos', path: TODOS_ROUTE, view: 'todos' },
  { id: 'cron', path: CRON_ROUTE, view: 'cron' },
  { id: 'profiles', path: PROFILES_ROUTE, view: 'profiles' },
  { id: 'agents', path: AGENTS_ROUTE, view: 'agents' }
] as const satisfies readonly AppRoute[]

const APP_VIEW_BY_PATH = new Map<string, AppView>(APP_ROUTES.map(route => [route.path, route.view]))
// Paths of retired pages (AIS-444: messaging). The router redirects them to a
// new chat; they must never be read as a session id in the meantime.
const RETIRED_PATHS = ['/messaging']

const RESERVED_PATHS: ReadonlySet<string> = new Set([...APP_ROUTES.map(route => route.path), ...RETIRED_PATHS])

// Views that render as a full-screen modal card (OverlayView) over the shell.
// While one is open the app's titlebar control clusters must hide so they don't
// bleed over the overlay (they sit at a higher z-index than the overlay card).
export const OVERLAY_VIEWS: ReadonlySet<AppView> = new Set(['agents', 'command-center', 'cron', 'profiles', 'settings'])

export function isOverlayView(view: AppView): boolean {
  return OVERLAY_VIEWS.has(view)
}

export function isNewChatRoute(pathname: string): boolean {
  return pathname === NEW_CHAT_ROUTE
}

export function routeSessionId(pathname: string): string | null {
  if (!pathname.startsWith(SESSION_ROUTE_PREFIX) || RESERVED_PATHS.has(pathname)) {
    return null
  }

  const id = pathname.slice(SESSION_ROUTE_PREFIX.length)

  return id && !id.includes('/') ? decodeURIComponent(id) : null
}

export function sessionRoute(sessionId: string, profile?: string): string {
  const route = `${SESSION_ROUTE_PREFIX}${encodeURIComponent(sessionId)}`

  return profile ? `${route}?profile=${encodeURIComponent(profile)}` : route
}

export function appViewForPath(pathname: string): AppView {
  if (isNewChatRoute(pathname) || routeSessionId(pathname)) {
    return 'chat'
  }

  return APP_VIEW_BY_PATH.get(pathname) ?? 'chat'
}
