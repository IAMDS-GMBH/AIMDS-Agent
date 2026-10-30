import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n'

import { serviceDisplayName, toolDetail, toolLabel, toolTitle } from './tool-labels'

const de = TRANSLATIONS.de.assistant.tool.activity
const en = TRANSLATIONS.en.assistant.tool.activity

// Tool names from session 20261026_120348_ca58fc (AIS-744): the rows read
// "Mcp AIMDSSuiteMCP Mcp Memory Memory Read" before.
describe('toolTitle for MCP tools', () => {
  it('reads OpenProject searches with their query', () => {
    const args = { context: 'Urlaub 2026', mcp_server: 'AIMDSSuiteMCP', mcp_tool: 'mcp_openproject-pm_search_work_packages' }

    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_openproject_pm_search_work_packages', args, true, de)).toBe(
      'Suche Tickets in OpenProject: „Urlaub 2026“'
    )
    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_openproject_pm_search_work_packages', args, false, en)).toBe(
      'Searched tickets in OpenProject: “Urlaub 2026”'
    )
  })

  it('works from the registered name alone (reloaded history has no origin fields)', () => {
    const name = 'mcp_AIMDSSuiteMCP_mcp_openproject_pm_list_time_entries'

    expect(toolTitle(name, { work_package_id: 'IAMDS-477', user: 'me' }, true, de)).toBe(
      'Lese Zeitbuchungen aus OpenProject: „IAMDS-477“'
    )
    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package', { id: 'IAMDS-477' }, false, de)).toBe(
      'Ticket in OpenProject geöffnet: „IAMDS-477“'
    )
  })

  // go-mcp-openproject 0.7.0 (AIS-775): one update tool comments, relates and moves.
  it('names an update by what its arguments do', () => {
    const name = 'mcp_AIMDSSuiteMCP_mcp_openproject_pm_update_work_package'

    expect(toolTitle(name, { id: 'AIS-711', comment: 'Released.' }, true, de)).toBe('Kommentiere Ticket in OpenProject: „AIS-711“')
    expect(toolTitle(name, { id: 'AIS-711', comment: 'Released.' }, false, en)).toBe(
      'Commented on ticket in OpenProject: “AIS-711”'
    )
    expect(toolTitle(name, { id: 'AIS-711', status: 'Closed', comment: 'Released.' }, false, de)).toBe(
      'Ticket in OpenProject geändert und kommentiert: „AIS-711“'
    )
    expect(toolTitle(name, { id: 'AIS-711', add_relations: [{ to: 'AIS-729' }] }, true, de)).toBe(
      'Verknüpfe Ticket in OpenProject: „AIS-711“'
    )
    expect(toolTitle(name, { id: 'AIS-711', remove_relations: ['AIS-729'] }, false, en)).toBe(
      'Linked ticket in OpenProject: “AIS-711”'
    )
    expect(toolTitle(name, { id: 'AIS-711', status: 'Closed' }, false, de)).toBe('Ticket in OpenProject geändert: „AIS-711“')
  })

  it('tells a move preview from the move itself', () => {
    const name = 'mcp_op_pm_update_work_package'
    const preview = { id: 'AIS-700', project: 'LBBW', include_descendants: true }

    expect(toolTitle(name, preview, true, de)).toBe('Plane das Verschieben von Tickets in OpenProject: „AIS-700“')
    expect(toolTitle(name, preview, false, en)).toBe('Planned the move of tickets in OpenProject: “AIS-700”')
    expect(toolTitle(name, { ...preview, dry_run: false, move_offset: 1 }, true, de)).toBe(
      'Verschiebe Tickets in OpenProject: „AIS-700“'
    )
    expect(toolTitle(name, { id: 'AIS-701', project: 'LBBW', dry_run: false }, false, en)).toBe(
      'Moved ticket in OpenProject: “AIS-701”'
    )
  })

  it('shows the first keys of an ids call', () => {
    const name = 'mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package'

    expect(toolTitle(name, { ids: ['AIS-1', 'AIS-2', 'AIS-3'] }, false, de)).toBe(
      'Tickets in OpenProject geöffnet: „AIS-1, AIS-2 (+1)“'
    )
    expect(
      toolTitle('mcp_op_pm_update_work_package', { ids: ['AIS-1', 'AIS-2'], comment: 'x' }, true, en)
    ).toBe('Commenting on tickets in OpenProject: “AIS-1, AIS-2”')
  })

  it('uses the place object for memory tools and hides technical slugs', () => {
    const args = { slug: 'tool-mcp-aimdssuitemcp-mcp-openproject-pm-list-time-entries' }

    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_memory_memory_read', args, true, de)).toBe('Öffne Notizen im Gedächtnis')
    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_memory_memory_search', { query: 'Urlaub' }, false, en)).toBe(
      'Searched notes in memory: “Urlaub”'
    )
  })

  it('falls back to the service name for unknown areas', () => {
    expect(toolTitle('mcp_github_list_issues', { mcp_server: 'github', mcp_tool: 'list_issues' }, true, de)).toBe(
      'Lese Tickets aus github'
    )
    expect(toolLabel('mcp_AcmeCrmMCP_sync', { mcp_server: 'AcmeCrmMCP', mcp_tool: 'sync' }, en)?.pending).toBe(
      'Working in Acme Crm'
    )
  })
})

describe('toolTitle for built-in and internal tools', () => {
  it('keeps internal preparation steps neutral', () => {
    expect(toolTitle('tool_search', { query: 'openproject time entries' }, true, de)).toBe('Bereite mich vor')
    expect(toolTitle('mcp_AIMDSSuiteMCP_mcp_memory_memory_context', {}, false, de)).toBe('Vorbereitet')
    expect(toolTitle('sql', { query: 'SELECT * FROM absences' }, true, de)).toBe('Werte Daten aus')
  })

  it('localizes built-in steps and shows file names only', () => {
    expect(toolTitle('workdays', { action: 'absences' }, false, de)).toBe('Arbeitstage und Abwesenheiten geprüft')
    expect(toolTitle('read_file', { path: '/Users/me/Vault/Urlaubsplanung 2026.md' }, true, de)).toBe(
      'Lese Datei: „Urlaubsplanung 2026.md“'
    )
  })

  it('ignores English gateway summaries for built-ins', () => {
    expect(toolTitle('todo', { context: 'planning 3 task(s)', todos: [] }, true, de)).toBe('Aktualisiere Aufgaben')
  })

  it('leaves unknown built-in tools to the caller', () => {
    expect(toolLabel('some_new_tool', {}, de)).toBeNull()
  })
})

describe('helpers', () => {
  it('names services the way users know them', () => {
    expect(serviceDisplayName('AIMDSSuiteMCP')).toBe('AIMDS Suite')
    expect(serviceDisplayName('TempoMCP')).toBe('Tempo')
  })

  it('shortens long details', () => {
    expect(toolDetail({ query: 'x'.repeat(80) })).toHaveLength(48)
    expect(toolDetail({ url: 'https://example.com/a/b' })).toBe('https://example.com/a/b')
  })

  // AIS-793: the question row said only "Rückfrage gestellt 7m 8s".
  it('names what a question row asked', () => {
    const questions = [
      { question: 'Load test before or after the release?', topic: 'QS-38' },
      { question: 'Who signs off?', topic: 'QS-41' }
    ]

    expect(toolTitle('clarify', { question: 'Which region?' }, false, de)).toBe('Rückfrage gestellt: „Which region?“')
    expect(toolDetail({ questions })).toBe('QS-38 (+1)')
    expect(toolDetail({ questions: [{ question: 'Where should the notes go?' }] })).toBe('Where should the notes go?')
  })

  it('reports the area for the activity summary', () => {
    expect(toolLabel('mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package', {}, de)?.area).toBe('OpenProject')
    expect(toolLabel('workdays', {}, de)?.area).toBe('Arbeitszeit')
    expect(toolLabel('tool_search', {}, de)?.area).toBeUndefined()
  })
})
