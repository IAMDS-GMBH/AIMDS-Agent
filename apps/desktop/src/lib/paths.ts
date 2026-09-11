// Bare file-path detection shared by the artifacts index (harvesting paths
// from transcripts) and the chat markdown autolinker (AIS-305 B).
//
// Group 1 is the boundary before the path (start of text, whitespace, an
// opening paren or a quote); group 2 is the path itself. A match requires:
//   • a rooted prefix — `/`, `~/`, `./`, `../` or a Windows drive `C:\`
//   • a file extension (1–8 alphanumerics) — so URL-ish paths like `/api/cron`
//     and plain directories don't turn into links
// Paths with spaces are not matched; the assistant wraps those in backticks,
// which the inline-code renderer handles separately.
export const FILE_PATH_RE =
  /(^|[\s("'`])((?:~\/|\.\.?\/|\/|[A-Za-z]:\\)[^\s"'`<>()]*\.[A-Za-z0-9]{1,8})(?=$|[\s"'`<>()]|[.,;:!?](?:\s|$))/g

// Same shape as one FILE_PATH_RE match, anchored to the whole string.
const BARE_PATH_RE = /^(?:~\/|\.\.?\/|\/|[A-Za-z]:\\)[^\s"'`<>()]*\.[A-Za-z0-9]{1,8}$/

const URL_SCHEME_TAIL_RE = /[A-Za-z][A-Za-z0-9+.-]*:\/\/?$/

// True when the whole string is one file path (an inline-code token like
// `` `~/notes/2026-09-08.md` ``).
export function isBareFilePath(value: string): boolean {
  return BARE_PATH_RE.test(value.trim())
}

// A match is "linkable" unless it's already the target of a markdown link
// (`](/x.md`), an autolink (`</x.md`) or the path part of a URL (`://x/y.md`).
export function isLinkablePathContext(text: string, pathIndex: number): boolean {
  const before = text.slice(Math.max(0, pathIndex - 12), pathIndex)

  if (before.endsWith('](') || before.endsWith('<')) {
    return false
  }

  return !URL_SCHEME_TAIL_RE.test(before)
}

export interface BarePathMatch {
  index: number
  path: string
}

// Every linkable bare path in `text`, in order.
export function findBarePaths(text: string): BarePathMatch[] {
  const out: BarePathMatch[] = []

  for (const match of text.matchAll(FILE_PATH_RE)) {
    const path = match[2] || ''
    const index = (match.index ?? 0) + (match[1] || '').length

    if (path && isLinkablePathContext(text, index)) {
      out.push({ index, path })
    }
  }

  return out
}
