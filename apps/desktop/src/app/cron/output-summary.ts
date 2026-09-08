import type { CronJobOutputSummary } from '@/types/hermes'

export interface CronOutputSummary {
  finding: string
  next: string
  openQuestion: string
}

const EMPTY_SUMMARY: CronOutputSummary = { finding: '', next: '', openQuestion: '' }

// A journal line like `FINDING: …`, tolerating list bullets (`- `, `* `, `1. `),
// bold markers around the label (`**FINDING:**` / `**FINDING**:`), an optional
// bold wrapper after the colon and trailing whitespace.
const SUMMARY_LINE_RE =
  /^\s*(?:[-*+•]|\d+[.)])?\s*(?:\*\*|__)?\s*(FINDING|NEXT|OPEN[_ ]QUESTION)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*?)\s*(?:\*\*|__)?\s*$/i

function cleanValue(value: string): string {
  return value
    .replace(/^(?:\*\*|__)\s*/, '')
    .replace(/\s*(?:\*\*|__)$/, '')
    .trim()
}

// Pull the FINDING / NEXT / OPEN_QUESTION lines out of a journal markdown body.
// First occurrence wins; labels are case-insensitive. Text without the markers
// yields empty strings so callers can fall back to a "no summary" state.
export function parseCronOutputSummary(text: null | string | undefined): CronOutputSummary {
  if (!text) {
    return { ...EMPTY_SUMMARY }
  }

  const out: CronOutputSummary = { ...EMPTY_SUMMARY }

  for (const rawLine of text.split(/\r?\n/)) {
    const match = SUMMARY_LINE_RE.exec(rawLine)

    if (!match) {
      continue
    }

    const label = match[1].toUpperCase().replace(' ', '_')
    const value = cleanValue(match[2] ?? '')

    if (label === 'FINDING' && !out.finding) {
      out.finding = value
    } else if (label === 'NEXT' && !out.next) {
      out.next = value
    } else if (label === 'OPEN_QUESTION' && !out.openQuestion) {
      out.openQuestion = value
    }

    if (out.finding && out.next && out.openQuestion) {
      break
    }
  }

  return out
}

// Backend-provided summary (snake_case) → the renderer's shape. Missing or
// blank fields stay empty so a partial server summary still merges cleanly
// with a local parse.
export function summaryFromApi(summary: CronJobOutputSummary | null | undefined): CronOutputSummary {
  const pick = (value: unknown) => (typeof value === 'string' ? value.trim() : '')

  return {
    finding: pick(summary?.finding),
    next: pick(summary?.next),
    openQuestion: pick(summary?.open_question)
  }
}

export function hasSummaryContent(summary: CronOutputSummary): boolean {
  return Boolean(summary.finding || summary.next || summary.openQuestion)
}
