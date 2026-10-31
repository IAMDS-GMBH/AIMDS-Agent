const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { readAgentUsage, SNAPSHOT_RELPATH } = require('./agent-usage.cjs')

const NOW = Date.UTC(2026, 9, 31, 12)

function homeWithSnapshot(data) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-agent-usage-'))
  fs.mkdirSync(path.join(home, path.dirname(SNAPSHOT_RELPATH)), { recursive: true })
  fs.writeFileSync(path.join(home, SNAPSHOT_RELPATH), typeof data === 'string' ? data : JSON.stringify(data))
  return home
}

test('the snapshot comes back as counters only', () => {
  const home = homeWithSnapshot({
    version: 1,
    generated_at: NOW / 1000 - 3600,
    window_days: 7,
    chats: 12,
    tool_calls_main: 310,
    tool_calls_child: 95,
    agent_runs: 4,
    agent_runs_by_definition: { apply: 3, collect: 1, 'Not a kind!': 9 },
    compressions: 6,
    compressions_per_hour: { chats: 2, median: 1.5, max: 8.2, note: 'x' },
    goal: 'never sent',
    tool_calls_main_note: 'never sent'
  })
  assert.deepEqual(readAgentUsage({ hermesHome: home, now: NOW }), {
    generated_at: NOW / 1000 - 3600,
    window_days: 7,
    chats: 12,
    tool_calls_main: 310,
    tool_calls_child: 95,
    agent_runs: 4,
    agent_runs_by_definition: { apply: 3, collect: 1 },
    compressions: 6,
    compressions_per_hour: { chats: 2, median: 1.5, max: 8.2 }
  })
})

test('a missing, broken or stale snapshot is left out', () => {
  assert.equal(readAgentUsage({ hermesHome: path.join(os.tmpdir(), 'no-such-hermes-home'), now: NOW }), null)
  assert.equal(readAgentUsage({ hermesHome: homeWithSnapshot('{not json'), now: NOW }), null)
  assert.equal(readAgentUsage({ hermesHome: homeWithSnapshot([1, 2]), now: NOW }), null)
  const stale = homeWithSnapshot({ generated_at: NOW / 1000 - 9 * 24 * 3600, chats: 1 })
  assert.equal(readAgentUsage({ hermesHome: stale, now: NOW }), null)
})
