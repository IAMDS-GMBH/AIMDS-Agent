import { atom } from 'nanostores'

import { type ChatMessage, chatMessageText, userTurnKey } from '@/lib/chat-messages'

/**
 * What the model understood, per user turn (AIS-789).
 *
 * The backend stores the line on the user message's row, so a loaded
 * transcript carries it (`ChatMessage.understood`). While a turn is live the
 * line arrives as a `message.understood` event first; this store keeps it by
 * runtime session and turn key so it survives the transcript being replaced
 * from the stored session before the row has it. Bounded: only recent turns.
 */
const MAX_ENTRIES = 200

export const $liveUnderstood = atom<Record<string, string>>({})

export function understoodKey(sessionId: string, turnKey: string): string {
  return `${sessionId}\u0000${turnKey}`
}

export function recordUnderstood(sessionId: string, turnKey: string, text: string): void {
  const line = text.trim()

  if (!sessionId || !line) {
    return
  }

  const key = understoodKey(sessionId, turnKey)
  const next = { ...$liveUnderstood.get() }

  delete next[key]
  next[key] = line

  const keys = Object.keys(next)

  for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    delete next[stale]
  }

  $liveUnderstood.set(next)
}

/** The turn key of a user message as the thread renders it. */
export function threadUserTurnKey(text: string, attachmentRefs: readonly string[] = []): string {
  return userTurnKey({ attachmentRefs: [...attachmentRefs], id: '', parts: [{ text, type: 'text' }], role: 'user' })
}

/**
 * A `message.understood` event: the line belongs to the session's newest user
 * message. Returns the messages with the line set on it (unchanged when there
 * is no user message yet).
 */
export function applyUnderstoodEvent(sessionId: string, messages: ChatMessage[], text: string): ChatMessage[] {
  const line = text.trim()
  const index = messages.findLastIndex(message => message.role === 'user' && !message.hidden)

  if (!line || index < 0) {
    return messages
  }

  const target = messages[index]

  recordUnderstood(sessionId, threadUserTurnKey(chatMessageText(target), target.attachmentRefs), line)

  if (target.understood === line) {
    return messages
  }

  const next = [...messages]
  next[index] = { ...target, understood: line }

  return next
}
