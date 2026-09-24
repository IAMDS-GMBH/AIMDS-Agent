const collapseWhitespace = (text: string) => text.replace(/\s+/g, ' ').trim()

/**
 * The part of an interim assistant message not yet in the stream buffer
 * (AIS-411, SUP-20260924-073844). The gateway re-sends the whole interim text
 * as `message.delta {interim: true}` whenever the agent could not prove it
 * was streamed; appended as-is, text already shown appeared twice. Already
 * contained → nothing; streamed as a prefix → only the rest; unrelated → the
 * interim text as a new paragraph.
 */
export function interimRemainder(buffered: string, interim: string): string {
  const shown = collapseWhitespace(buffered)
  const next = collapseWhitespace(interim)

  if (!next) {
    return ''
  }

  if (shown && shown.includes(next)) {
    return ''
  }

  if (shown && next.startsWith(shown)) {
    let consumed = 0

    while (consumed < interim.length && collapseWhitespace(interim.slice(0, consumed)).length < shown.length) {
      consumed += 1
    }

    return interim.slice(consumed)
  }

  const trimmed = interim.trim()

  return buffered.trim() ? `\n\n${trimmed}` : trimmed
}
