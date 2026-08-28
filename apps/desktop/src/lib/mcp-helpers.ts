import type { McpServerSummary } from '@/types/hermes'

export function getMcpServerToolCount(server: McpServerSummary): number | null {
  if (Array.isArray(server.tools)) {
    return server.tools.length
  }

  if (server.tools && typeof server.tools === 'object' && Array.isArray((server.tools as any).include)) {
    return (server.tools as any).include.length
  }

  return null
}
