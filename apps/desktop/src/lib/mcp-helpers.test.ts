import { describe, expect, it } from 'vitest'

import {
  getMcpServerConfiguredToolCount,
  getMcpServerToolCount,
  isMcpServerConfiguredButUnloaded
} from './mcp-helpers'

/**
 * The counts shown in the status bar and the connection panel used to come
 * from the `tools.include` allow-list in config. That is a wish, not a fact:
 * it names tools a server may not expose, and it stays populated when nothing
 * is loaded at all. A backend with an empty MCP registry therefore reported
 * "6/6 (91 Tools)" and "TempoMCP aktiv (14 Tools)" while every MCP call in the
 * session failed with "is not available".
 */
const server = (over: Record<string, unknown> = {}) =>
  ({ name: 'TempoMCP', enabled: true, ...over }) as any

describe('getMcpServerToolCount', () => {
  it('reports what the server actually registered', () => {
    expect(getMcpServerToolCount(server({ discovered_tools: ['a', 'b', 'c'] }))).toBe(3)
  })

  it('reports zero rather than the configured wish when nothing loaded', () => {
    const s = server({ discovered_tools: [], tools: { include: new Array(14).fill('t') } })

    expect(getMcpServerToolCount(s)).toBe(0)
  })

  it('never falls back to the config list', () => {
    // TempoMCP lists 14 in config and registers 7 — the headline must be 7.
    const s = server({ discovered_tools: new Array(7).fill('t'), tools: new Array(14).fill('t') })

    expect(getMcpServerToolCount(s)).toBe(7)
  })

  it('is null when the backend said nothing about discovery', () => {
    expect(getMcpServerToolCount(server({ tools: ['a'] }))).toBeNull()
  })
})

describe('getMcpServerConfiguredToolCount', () => {
  it('reads a plain array', () => {
    expect(getMcpServerConfiguredToolCount(server({ tools: ['a', 'b'] }))).toBe(2)
  })

  it('reads the include allow-list', () => {
    expect(getMcpServerConfiguredToolCount(server({ tools: { include: ['a'] } }))).toBe(1)
  })

  it('is null when unconstrained', () => {
    expect(getMcpServerConfiguredToolCount(server())).toBeNull()
  })
})

describe('isMcpServerConfiguredButUnloaded', () => {
  it('flags the exact state that used to look healthy', () => {
    const s = server({ discovered_tools: [], tools: { include: new Array(14).fill('t') } })

    expect(isMcpServerConfiguredButUnloaded(s)).toBe(true)
  })

  it('does not flag a server that loaded its tools', () => {
    const s = server({ discovered_tools: ['a'], tools: { include: ['a', 'b'] } })

    expect(isMcpServerConfiguredButUnloaded(s)).toBe(false)
  })

  it('does not flag a server with nothing configured either', () => {
    expect(isMcpServerConfiguredButUnloaded(server({ discovered_tools: [] }))).toBe(false)
  })
})
