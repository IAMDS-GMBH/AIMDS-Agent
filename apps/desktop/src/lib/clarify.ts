// AIS-793: questions the agent asks in one card and the answers it gets back —
// the clarify_questions contract (agent/contracts/clarify_questions.schema.json,
// generated into apps/shared/src/contracts.ts by AIS-797). Mirrored here until
// that lands; the shapes match it field for field.

export interface ClarifyQuestion {
  choices?: string[]
  question: string
  /** Short label that groups questions in the card ("QS-38"). */
  topic?: string
}

/** Same values as the clarify tool's response_state; "skipped" is "later". */
export type ClarifyAnswerState = 'answered' | 'delivery_failed' | 'empty' | 'skipped' | 'timeout' | 'unavailable'

export interface ClarifyAnswer {
  response: string
  state: ClarifyAnswerState
}

const ANSWER_STATES = new Set<ClarifyAnswerState>([
  'answered',
  'delivery_failed',
  'empty',
  'skipped',
  'timeout',
  'unavailable'
])

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function parseJson(value: unknown): unknown {
  if (typeof value !== 'string' || !/^\s*[[{]/.test(value)) {
    return value
  }

  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((c): c is string => typeof c === 'string' && c.trim() !== '') : []
}

function toQuestion(question: string, choices: unknown, topic: unknown): ClarifyQuestion {
  const options = stringList(choices)
  const label = typeof topic === 'string' ? topic.trim() : ''

  return {
    question: question.trim(),
    ...(options.length ? { choices: options } : {}),
    ...(label ? { topic: label } : {})
  }
}

/** `questions` from a clarify.request payload or the tool arguments. */
export function readClarifyQuestions(value: unknown): ClarifyQuestion[] {
  const list = parseJson(value)

  if (!Array.isArray(list)) {
    return []
  }

  return list.flatMap(item => {
    if (typeof item === 'string') {
      return item.trim() ? [toQuestion(item, null, null)] : []
    }

    const row = record(item)
    const text = typeof row?.question === 'string' ? row.question : ''

    return text.trim() ? [toQuestion(text, row?.choices, row?.topic)] : []
  })
}

/** The questions of a clarify call from its arguments (the single form is one). */
export function clarifyArgsQuestions(args: unknown): ClarifyQuestion[] {
  const row = record(parseJson(args)) ?? {}
  const several = readClarifyQuestions(row.questions)

  if (several.length) {
    return several
  }

  return typeof row.question === 'string' && row.question.trim() ? [toQuestion(row.question, row.choices, null)] : []
}

export interface ClarifyOutcomeRow extends ClarifyQuestion, ClarifyAnswer {}

function answerState(value: unknown, response: string): ClarifyAnswerState {
  const state = typeof value === 'string' ? (value.toLowerCase() as ClarifyAnswerState) : ''

  if (state && ANSWER_STATES.has(state)) {
    return state
  }

  return response ? 'answered' : 'empty'
}

/** Question → answer rows of a finished clarify call; null when it failed
 *  (an error result renders as a plain tool row). */
export function readClarifyOutcome(args: unknown, result: unknown): ClarifyOutcomeRow[] | null {
  const parsed = parseJson(result)
  const row = record(parsed)

  if (row && typeof row.error === 'string' && row.error) {
    return null
  }

  const asked = clarifyArgsQuestions(args)

  const answers = row?.answers

  if (row && Array.isArray(answers)) {
    const questions = readClarifyQuestions(row.questions)
    const items = questions.length ? questions : asked

    return items.map((item, index) => {
      const answer = record(answers[index])
      const response = typeof answer?.response === 'string' ? answer.response : ''

      return { ...item, response, state: answer ? answerState(answer.state, response) : 'empty' }
    })
  }

  // The single form before AIS-793, and plain-string results of older sessions.
  const question = (typeof row?.question === 'string' && row.question) || asked[0]?.question || ''

  if (!question) {
    return null
  }

  const response = row
    ? typeof row.user_response === 'string'
      ? row.user_response.trim()
      : ''
    : typeof parsed === 'string'
      ? parsed.trim()
      : ''

  return [{ ...(asked[0] ?? {}), question, response, state: answerState(row?.response_state, response) }]
}
