const REASONING_LABELS: Record<string, string> = {
  none: 'Off',
  minimal: 'Min',
  low: 'Low',
  medium: 'Med',
  high: 'High',
  xhigh: 'Max'
}

export function reasoningEffortLabel(effort: string): string {
  const key = effort.trim().toLowerCase()

  if (!key) {
    return ''
  }

  return REASONING_LABELS[key] ?? effort
}

/** Strip provider prefix and normalize for display. */
export function modelBaseId(model: string): string {
  const trimmed = model.trim()
  const slash = trimmed.lastIndexOf('/')

  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed
}

// Trailing model-id variants that should render as a grayed tag beside the
// name (e.g. "Opus 4.8" + "Fast") rather than collapsing two distinct ids to
// the same display name.
const VARIANT_TAGS: ReadonlyArray<readonly [RegExp, string]> = [
  [/-fast$/i, 'Fast'],
  [/-thinking$/i, 'Thinking'],
  [/-preview$/i, 'Preview'],
  [/-latest$/i, 'Latest']
]

const titleCase = (text: string): string => text.replace(/\b\w/g, char => char.toUpperCase()).trim()

function prettifyBase(base: string): string {
  if (/^claude-/i.test(base)) {
    return titleCase(base.replace(/^claude-/i, '').replace(/-/g, ' '))
  }

  if (/^gpt-/i.test(base)) {
    return base.replace(/^gpt-/i, 'GPT-')
  }

  if (/^gemini-/i.test(base)) {
    return base.replace(/^gemini-/i, 'Gemini ').replace(/-/g, ' ')
  }

  return titleCase(base.replace(/-/g, ' '))
}

/** Split a model id into a clean display name plus an optional grayed variant
 *  tag, so distinct ids (e.g. `…-4.8` vs `…-4.8-fast`) don't collapse. */
export function modelDisplayParts(model: string): { name: string; tag: string } {
  let base = modelBaseId(model)
  let tag = ''

  for (const [pattern, label] of VARIANT_TAGS) {
    if (pattern.test(base)) {
      tag = label
      base = base.replace(pattern, '')

      break
    }
  }

  return { name: prettifyBase(base) || model.trim() || 'No model', tag }
}

/** Friendly one-line model name for menus and the status bar. */
export function displayModelName(model: string): string {
  return modelDisplayParts(model).name
}

const AIMDS_PROVIDER_NAMES: Record<string, string> = {
  'aimds-suite-prod': 'Productive',
  'aimds-suite-staging': 'Staging',
  'aimds-suite-dev': 'Development',
  'iamds-litellm': 'Productive',
  'iamds-litellm-staging': 'Staging',
  'iamds-litellm-dev': 'Development',
  'iamds_litellm': 'Productive',
  'iamds_litellm_staging': 'Staging',
  'iamds_litellm_dev': 'Development',
  'iamds': 'Productive',
  'aimds': 'Productive'
}

export function formatAimdsProviderLabel(provider: string): string | null {
  const norm = provider.trim().toLowerCase()
  return AIMDS_PROVIDER_NAMES[norm] ?? null
}

/** Status bar trigger label — model name plus the live session state (effort/fast). */
export function formatModelStatusLabel(
  model: string,
  options?: {
    fastMode?: boolean
    reasoningEffort?: string
    provider?: string
    toolsCount?: number | null
  }
): string {
  const name = displayModelName(model)
  const rawProvider = options?.provider?.trim() ?? ''
  const aimdsLabel = formatAimdsProviderLabel(rawProvider)

  if (!model.trim()) {
    return aimdsLabel ? `${aimdsLabel} · No model` : name
  }

  const parts: string[] = []

  if (aimdsLabel) {
    parts.push(aimdsLabel)
  }

  parts.push(name)

  const stateFlags: string[] = []
  if (options?.fastMode || /-fast$/i.test(modelBaseId(model))) {
    stateFlags.push('Fast')
  }

  const effort = reasoningEffortLabel(options?.reasoningEffort ?? '') || 'Med'
  stateFlags.push(effort)

  if (options?.fastMode || (options?.reasoningEffort && options.reasoningEffort !== 'medium') || !aimdsLabel) {
    parts.push(stateFlags.join(' '))
  }

  if (typeof options?.toolsCount === 'number') {
    parts.push(`${options.toolsCount} Tool${options.toolsCount === 1 ? '' : 's'}`)
  }

  return parts.join(' · ')
}
