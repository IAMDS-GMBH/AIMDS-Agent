'use strict'

// Agent usage counters for the client telemetry (AIS-796).
//
// The chats write HERMES_HOME/telemetry/agent-usage.json (agent/usage_telemetry.py):
// tool calls of the chats vs. of their subagent runs, subagent runs per agent
// kind and context compressions per hour per chat. This reads it back for the
// telemetry payload and keeps only those numbers — whatever else the file
// holds never leaves the machine. A snapshot older than eight days is stale
// (the window is seven) and is left out.

const fs = require('node:fs')
const path = require('node:path')

const SNAPSHOT_RELPATH = path.join('telemetry', 'agent-usage.json')
const MAX_AGE_MS = 8 * 24 * 3600 * 1000
const COUNTER_KEYS = ['window_days', 'chats', 'tool_calls_main', 'tool_calls_child', 'agent_runs', 'compressions']
const RATE_KEYS = ['chats', 'median', 'max']
const KIND_NAME = /^[a-z][a-z0-9-]{1,40}$/

const isRecord = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

function numbersOnly(source, keys) {
  const out = {}
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) out[key] = value
  }
  return out
}

function readAgentUsage({ hermesHome, fsImpl = fs, now = Date.now() } = {}) {
  let data
  try {
    data = JSON.parse(fsImpl.readFileSync(path.join(String(hermesHome || ''), SNAPSHOT_RELPATH), 'utf8'))
  } catch {
    return null
  }
  if (!isRecord(data)) return null
  const generatedAt = data.generated_at
  if (typeof generatedAt !== 'number' || !Number.isFinite(generatedAt) || now - generatedAt * 1000 > MAX_AGE_MS) {
    return null
  }
  const usage = { generated_at: generatedAt, ...numbersOnly(data, COUNTER_KEYS) }
  if (isRecord(data.agent_runs_by_definition)) {
    const byKind = {}
    for (const [name, count] of Object.entries(data.agent_runs_by_definition)) {
      if (KIND_NAME.test(name) && typeof count === 'number' && Number.isFinite(count) && count >= 0)
        byKind[name] = count
    }
    usage.agent_runs_by_definition = byKind
  }
  if (isRecord(data.compressions_per_hour))
    usage.compressions_per_hour = numbersOnly(data.compressions_per_hour, RATE_KEYS)
  return usage
}

module.exports = { readAgentUsage, SNAPSHOT_RELPATH }
