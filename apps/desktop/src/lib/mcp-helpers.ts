import type { McpServerSummary } from '@/types/hermes'

/**
 * Tools this server has actually registered, or null when unknown.
 *
 * `discovered_tools` is what the backend loaded and the agent can call.
 * `tools` is the `include` allow-list from config — a wish, not a fact: it can
 * name tools the server does not expose (TempoMCP lists 14 and registers 7),
 * and it stays populated even when nothing is loaded at all. Reporting the
 * config number made a backend with an empty tool registry look healthy
 * ("6/6, 91 Tools") while every MCP call in the session failed.
 */
export function getMcpServerToolCount(server: McpServerSummary): number | null {
  if (Array.isArray(server.discovered_tools)) {
    return server.discovered_tools.length
  }

  return null
}

/**
 * Tools the config asks for, independent of what loaded. Use only to explain
 * a gap against {@link getMcpServerToolCount} — never as the headline number.
 */
export function getMcpServerConfiguredToolCount(server: McpServerSummary): number | null {
  if (Array.isArray(server.tools)) {
    return server.tools.length
  }

  if (server.tools && typeof server.tools === 'object' && Array.isArray((server.tools as any).include)) {
    return (server.tools as any).include.length
  }

  return null
}

/** True when the server is configured but has registered nothing. */
export function isMcpServerConfiguredButUnloaded(server: McpServerSummary): boolean {
  return getMcpServerToolCount(server) === 0 && (getMcpServerConfiguredToolCount(server) ?? 0) > 0
}
