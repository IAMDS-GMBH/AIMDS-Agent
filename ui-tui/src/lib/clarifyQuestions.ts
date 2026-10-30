import type { ClarifyAnswer, ClarifyQuestion, ClarifyReq } from '../types.js'

// AIS-793: one clarify.request can carry several questions. The TUI asks them
// one after another in its ClarifyPrompt and answers them together
// (clarify.respond `answers`, one per question in order).

const cleanQuestions = (raw: unknown): ClarifyQuestion[] =>
  Array.isArray(raw)
    ? raw.flatMap(item => {
        if (!item || typeof item !== 'object') {
          return []
        }

        const row = item as Record<string, unknown>
        const question = typeof row.question === 'string' ? row.question.trim() : ''

        if (!question) {
          return []
        }

        const choices = Array.isArray(row.choices) ? row.choices.filter((c): c is string => typeof c === 'string') : []
        const topic = typeof row.topic === 'string' ? row.topic.trim() : ''

        return [{ choices: choices.length ? choices : null, question, ...(topic ? { topic } : {}) }]
      })
    : []

/** The prompt text for one step: "(2/3) QS-38: …" (no prefix for a single question). */
export const clarifyStepQuestion = (questions: ClarifyQuestion[], index: number): string => {
  const item = questions[index]

  if (!item) {
    return ''
  }

  const text = item.topic ? `${item.topic}: ${item.question}` : item.question

  return questions.length > 1 ? `(${index + 1}/${questions.length}) ${text}` : text
}

/** The overlay request for a clarify.request payload. */
export const clarifyRequestFromPayload = (payload: {
  choices?: null | string[]
  question?: string
  questions?: unknown
  request_id: string
}): ClarifyReq => {
  const questions = cleanQuestions(payload.questions)

  if (questions.length > 1) {
    return {
      answers: [],
      choices: questions[0]!.choices ?? null,
      index: 0,
      question: clarifyStepQuestion(questions, 0),
      questions,
      requestId: payload.request_id
    }
  }

  return { choices: payload.choices ?? null, question: payload.question ?? '', requestId: payload.request_id }
}

/**
 * Apply the answer to the current step. With more questions to go, `next` is
 * the overlay for the next one; otherwise `next` is null and `answers` holds
 * one entry per question. An empty answer (Esc / Ctrl+C) ends the ask: the
 * remaining questions stay empty.
 */
export const advanceClarify = (req: ClarifyReq, answer: string): { answers: ClarifyAnswer[]; next: ClarifyReq | null } => {
  const questions = req.questions ?? []

  if (questions.length < 2) {
    return { answers: [{ response: answer, state: answer ? 'answered' : 'empty' }], next: null }
  }

  const index = req.index ?? 0
  const given: ClarifyAnswer[] = [...(req.answers ?? [])]

  if (!answer) {
    while (given.length < questions.length) {
      given.push({ response: '', state: 'empty' })
    }

    return { answers: given, next: null }
  }

  given.push({ response: answer, state: 'answered' })

  if (index + 1 >= questions.length) {
    return { answers: given, next: null }
  }

  return {
    answers: given,
    next: {
      ...req,
      answers: given,
      choices: questions[index + 1]!.choices ?? null,
      index: index + 1,
      question: clarifyStepQuestion(questions, index + 1)
    }
  }
}

/** The answered ask as one transcript block: "QS-38: … → Before". */
export const formatClarifyAnswers = (questions: ClarifyQuestion[], answers: ClarifyAnswer[]): string =>
  questions
    .map((item, i) => {
      const answer = answers[i]
      const label = item.topic ? `${item.topic}: ${item.question}` : item.question
      const value = answer?.state === 'answered' ? answer.response : '(no answer)'

      return `${label} → ${value}`
    })
    .join('\n')
