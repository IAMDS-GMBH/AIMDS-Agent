// YAML frontmatter for the markdown file preview. Without this the closing
// `---` turns the block into a setext heading ("type: proposal title: …").
const FRONTMATTER_RE = /^\uFEFF?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/
const PROPERTY_RE = /^([A-Za-z0-9_][\w.-]*)[ \t]*:(?:[ \t]+(.*))?$/
const LIST_ITEM_RE = /^[ \t]+-[ \t]+(.*)$/

export interface MarkdownFrontmatter {
  body: string
  /** Flat `key: value` pairs; null when the block is valid but not flat. */
  properties: [string, string][] | null
  raw: string
}

function unquote(value: string): string {
  const trimmed = value.trim()
  const quoted = /^(["'])(.*)\1$/.exec(trimmed)

  return quoted ? quoted[2] : trimmed
}

function inlineValue(value: string): string {
  const trimmed = value.trim()
  const list = /^\[(.*)\]$/.exec(trimmed)

  return list
    ? list[1]
        .split(',')
        .map(item => unquote(item))
        .filter(Boolean)
        .join(', ')
    : unquote(trimmed)
}

function parseFlatProperties(raw: string): [string, string][] | null {
  const properties: [string, string][] = []

  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim() || line.trim().startsWith('#')) {
      continue
    }

    const item = LIST_ITEM_RE.exec(line)

    if (item && properties.length > 0) {
      const last = properties[properties.length - 1]
      last[1] = [last[1], unquote(item[1])].filter(Boolean).join(', ')

      continue
    }

    const property = PROPERTY_RE.exec(line)

    if (!property) {
      return null
    }

    properties.push([property[1], inlineValue(property[2] ?? '')])
  }

  return properties.length > 0 ? properties : null
}

export function splitFrontmatter(text: string): MarkdownFrontmatter | null {
  const match = FRONTMATTER_RE.exec(text)

  if (!match) {
    return null
  }

  return {
    body: text.slice(match[0].length).replace(/^\s*\n/, ''),
    properties: parseFlatProperties(match[1]),
    raw: match[1]
  }
}
