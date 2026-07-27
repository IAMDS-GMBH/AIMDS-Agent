import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '@/lib/chat-messages'
import { textPart } from '@/lib/chat-messages'
import type { ComposerAttachment } from '@/store/composer'

import {
  coerceGatewayText,
  coerceThinkingText,
  isSyntheticContextNote,
  optimisticAttachmentRef,
  toRuntimeMessage
} from './chat-runtime'

const DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANS'

function attachment(overrides: Partial<ComposerAttachment> & Pick<ComposerAttachment, 'kind'>): ComposerAttachment {
  return { id: 'a', label: 'file.png', ...overrides }
}

describe('optimisticAttachmentRef', () => {
  it('renders an image from its in-hand base64 preview (no @image: path ref)', () => {
    const ref = optimisticAttachmentRef(attachment({ kind: 'image', detail: '/tmp/shot.png', previewUrl: DATA_URL }))

    // The raw data URL flows through extractEmbeddedImages → inline thumbnail,
    // dodging the remote /api/media 403 an @image:<localpath> ref would hit.
    expect(ref).toBe(DATA_URL)
  })

  it('falls back to an @image: path ref when no preview is available', () => {
    expect(optimisticAttachmentRef(attachment({ kind: 'image', detail: '/tmp/shot.png' }))).toBe('@image:/tmp/shot.png')
  })

  it('ignores a non-data preview url and uses the path ref', () => {
    const ref = optimisticAttachmentRef(
      attachment({ kind: 'image', detail: '/tmp/shot.png', previewUrl: 'https://example.com/x.png' })
    )

    expect(ref).toBe('@image:/tmp/shot.png')
  })

  it('passes non-image attachments straight through to attachmentDisplayText', () => {
    expect(optimisticAttachmentRef(attachment({ kind: 'file', refText: '@file:src/a.ts', previewUrl: DATA_URL }))).toBe(
      '@file:src/a.ts'
    )
  })
})

describe('coerceThinkingText', () => {
  it('strips streaming status prefixes from thinking deltas', () => {
    expect(coerceThinkingText("◉_◉ processing... checking the user's request")).toBe("checking the user's request")
    expect(coerceThinkingText('(¬‿¬) analyzing... reading the file')).toBe('reading the file')
  })

  it('drops empty thinking rewrite placeholder text', () => {
    expect(
      coerceThinkingText(
        "◉_◉ processing... I don't see any current rewritten thinking or next thinking to process. Could you provide the thinking content you'd like me to rewrite?"
      )
    ).toBe('')
  })
})

describe('coerceGatewayText', () => {
  it('extracts nested text values from structured output_text content', () => {
    const value = [
      {
        type: 'output_text',
        text: {
          value: 'Clean assistant text',
          annotations: [{ type: 'file_citation', file_id: 'file_123' }]
        }
      }
    ]

    expect(coerceGatewayText(value)).toBe('Clean assistant text')
  })
})

function chatMessage(overrides: Partial<ChatMessage> & Pick<ChatMessage, 'id' | 'role'>): ChatMessage {
  return { parts: [], ...overrides }
}

describe('isSyntheticContextNote', () => {
  it('detects the todo-snapshot injection text (tools/todo_tool.py::format_for_injection)', () => {
    expect(
      isSyntheticContextNote(
        '[Your active task list was preserved across context compression]\n- [ ] example-task. Example task'
      )
    ).toBe(true)
  })

  it('detects the context-summary injection text (agent/context_compressor.py)', () => {
    expect(
      isSyntheticContextNote(
        'Some summary body\n--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---'
      )
    ).toBe(true)
  })

  it('does not flag ordinary user/assistant text', () => {
    expect(isSyntheticContextNote('Can you check the vault folder path?')).toBe(false)
  })
})

describe('toRuntimeMessage — synthetic context notes', () => {
  it('reclassifies a todo-snapshot message stored as role:"user" to role:"system"', () => {
    const message = chatMessage({
      id: 'm1',
      role: 'user',
      parts: [
        textPart(
          '[Your active task list was preserved across context compression]\n- [ ] example-task. Example task'
        )
      ]
    })

    expect(toRuntimeMessage(message).role).toBe('system')
  })

  it('reclassifies a context-summary message stored as role:"assistant" to role:"system"', () => {
    const message = chatMessage({
      id: 'm2',
      role: 'assistant',
      parts: [
        textPart(
          'Summary of prior work...\n--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---'
        )
      ]
    })

    expect(toRuntimeMessage(message).role).toBe('system')
  })

  it('leaves ordinary user messages as role:"user"', () => {
    const message = chatMessage({ id: 'm3', role: 'user', parts: [textPart('hello there')] })

    expect(toRuntimeMessage(message).role).toBe('user')
  })
})
