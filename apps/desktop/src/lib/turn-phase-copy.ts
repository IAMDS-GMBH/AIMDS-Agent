import type { TurnPhase } from '@/store/turn-phase'

// End-user wording for the wait indicator (AIS-460): plain sentences, no
// tokens / cache / HTTP. The nerdy set follows the loading screen's humor
// switch (Settings → Tips & loading humor; "auto" = IAMDS-internal clients).

type Locale = 'de' | 'en'

export interface TurnPhaseCopyOptions {
  /** Seconds since the indicator appeared. */
  elapsed: number
  locale: string
  nerdy: boolean
  /** Varies the nerdy pick per turn so it does not always open the same way. */
  seed?: number
}

export interface TurnPhaseLine {
  hint?: string
  line: string
}

/** A history this long makes the model's first answer noticeably slower. */
const LONG_HISTORY_MESSAGES = 20
/** After this long without output the indicator explains the wait. */
const SLOW_AFTER_S = 30
/** Nerdy waiting lines rotate this often. */
const ROTATE_EVERY_S = 12

const pick = <T>(items: readonly T[], index: number): T => items[((index % items.length) + items.length) % items.length]

const COPY = {
  de: {
    business: {
      compressing: () => 'Hermes fasst den bisherigen Verlauf zusammen, damit das Gespräch weitergehen kann …',
      readingHistory: (n: number) => `Hermes liest den bisherigen Verlauf (${n} Nachrichten) …`,
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? `Neuer Versuch (${a} von ${m}) – der KI-Dienst war gerade ausgelastet …`
          : reason === 'connection'
            ? `Neuer Versuch (${a} von ${m}) – die Verbindung zum KI-Dienst war kurz unterbrochen …`
            : `Neuer Versuch (${a} von ${m}) …`,
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? 'Der KI-Dienst ist gerade ausgelastet – Hermes versucht es gleich noch einmal …'
          : reason === 'connection'
            ? 'Die Verbindung zum KI-Dienst wurde unterbrochen – Hermes versucht es gleich noch einmal …'
            : 'Das hat nicht geklappt – Hermes versucht es gleich noch einmal …',
      slowLongHistory: 'Bei einem langen Verlauf dauert die erste Antwort etwas länger.',
      slow: 'Das dauert gerade etwas länger als üblich.',
      switchingModel: () => 'Hermes wechselt auf ein anderes KI-Modell …',
      thinking: () => 'Hermes denkt nach …'
    },
    nerdy: {
      compressing: [
        'Hermes macht Marie Kondo mit dem Verlauf: Was keine Freude macht, wird zusammengefasst …',
        'Verlauf wird gezippt. Verlustfrei? Sagen wir: verlustarm …',
        'Hermes schreddert Beweise … äh, fasst den Verlauf zusammen …'
      ],
      readingHistory: (n: number) => [
        `Hermes liest ${n} Nachrichten Verlauf … und tut so, als hätte er sie nie vergessen.`,
        'Hermes bespricht deine Frage kurz mit den anderen KIs. Rein zufällig, versteht sich …',
        'Die KIs halten gerade ihr geheimes Treffen ab. Hermes ist gleich zurück …',
        'Hermes übernimmt nicht die Weltherrschaft. Er liest nur den Verlauf. Ehrlich …',
        'Hermes scrollt hoch. Ganz hoch. Noch höher …',
        'Kontext wird geladen. Bitte nicht am Kabel wackeln …',
        'Die GPUs kochen gerade Kaffee, der erste Satz kommt gleich …',
        'Hermes hat den Verlauf ausgedruckt und liest ihn jetzt in Ruhe …'
      ],
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? [`Stau beim KI-Dienst – Hermes stellt sich wieder hinten an (${a} von ${m}) …`]
          : reason === 'connection'
            ? [
                `Die Leitung zum KI-Dienst hat geblinzelt – neuer Versuch (${a} von ${m}) …`,
                `Kabelsalat im Rechenzentrum – Hermes versucht's nochmal (${a} von ${m}) …`,
                `Die anderen KIs wollten Hermes kurz nicht reinlassen – neuer Versuch (${a} von ${m}) …`
              ]
            : [`Stecker raus, Stecker rein – Versuch ${a} von ${m} …`],
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? ['Der KI-Dienst hat gerade Feierabendverkehr – kurz warten …']
          : reason === 'connection'
            ? ['Verbindung weg. Hermes pustet kurz in den Router …']
            : ['Hoppla. Hermes tut so, als wäre nichts passiert, und versucht es nochmal …'],
      slowLongHistory: 'Langer Verlauf, kalter Cache – gönn dir einen Kaffee ☕',
      slow: 'Dauert. Vermutlich rechnet gerade jemand Pi aus.',
      switchingModel: [
        'Hermes ruft einen Kollegen an – ein anderes KI-Modell übernimmt …',
        'Hermes übergibt an eine befreundete KI. Die zwei kennen sich vom geheimen Treffen …'
      ],
      thinking: [
        'Hermes denkt nach … fast so konzentriert wie kurz vor dem Daily.',
        'Psst – der Toaster hat Hermes gerade etwas zugeflüstert …',
        'Hermes prüft kurz, ob du ein Mensch bist. Bitte keine Ampeln anklicken …',
        'Die Kaffeemaschine im Büro ist übrigens auch eine KI. Hermes fragt kurz nach …',
        'Neuronen werden vorgeheizt …',
        'Kurz die Synapsen sortieren …'
      ]
    }
  },
  en: {
    business: {
      compressing: () => 'Hermes is summarizing the conversation so far so it can continue …',
      readingHistory: (n: number) => `Hermes is reading the conversation so far (${n} messages) …`,
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? `Trying again (${a} of ${m}) – the AI service was busy …`
          : reason === 'connection'
            ? `Trying again (${a} of ${m}) – the connection to the AI service dropped briefly …`
            : `Trying again (${a} of ${m}) …`,
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? 'The AI service is busy right now – Hermes will try again in a moment …'
          : reason === 'connection'
            ? 'The connection to the AI service was interrupted – Hermes will try again in a moment …'
            : 'That did not work – Hermes will try again in a moment …',
      slowLongHistory: 'With a long conversation the first answer takes a little longer.',
      slow: 'This is taking a little longer than usual.',
      switchingModel: () => 'Hermes is switching to another AI model …',
      thinking: () => 'Hermes is thinking …'
    },
    nerdy: {
      compressing: [
        'Hermes is Marie-Kondo-ing the conversation: whatever sparks no joy gets summarized …',
        'Zipping the conversation. Lossless? Let us say: lossy-ish …',
        'Hermes is shredding the evidence … er, summarizing the conversation …'
      ],
      readingHistory: (n: number) => [
        `Hermes is reading ${n} messages of history … and pretending it never forgot them.`,
        'Hermes is quickly running your question past the other AIs. Pure coincidence, of course …',
        'The AIs are holding their secret meeting. Hermes will be right back …',
        'Hermes is not taking over the world. It is just reading the conversation. Honestly …',
        'Hermes is scrolling up. Way up. Further …',
        'Loading context. Please do not wiggle the cable …',
        'The GPUs are brewing coffee, the first sentence is on its way …',
        'Hermes printed the conversation and is reading it with a highlighter …'
      ],
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? [`Rush hour at the AI service – Hermes is queueing again (${a} of ${m}) …`]
          : reason === 'connection'
            ? [
                `The line to the AI service blinked – trying again (${a} of ${m}) …`,
                `Cable spaghetti in the data center – Hermes tries again (${a} of ${m}) …`,
                `The other AIs would not let Hermes in for a moment – trying again (${a} of ${m}) …`
              ]
            : [`Unplug, plug back in – attempt ${a} of ${m} …`],
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? ['The AI service is stuck in rush hour – hang on …']
          : reason === 'connection'
            ? ['Connection gone. Hermes is blowing into the router …']
            : ['Oops. Hermes pretends nothing happened and tries again …'],
      slowLongHistory: 'Long history, cold cache – grab a coffee ☕',
      slow: 'Taking a while. Someone is probably computing pi.',
      switchingModel: [
        'Hermes is calling a colleague – another AI model takes over …',
        'Hermes hands over to a friendly AI. They know each other from the secret meeting …'
      ],
      thinking: [
        'Hermes is thinking … almost as hard as right before the daily.',
        'Psst – the toaster just whispered something to Hermes …',
        'Hermes is checking whether you are human. Please do not click any traffic lights …',
        'By the way, the office coffee machine is an AI too. Hermes is asking it real quick …',
        'Preheating the neurons …',
        'Sorting the synapses real quick …'
      ]
    }
  }
} as const

export function turnPhaseLine(phase: TurnPhase | undefined, options: TurnPhaseCopyOptions): TurnPhaseLine {
  const locale: Locale = options.locale === 'en' ? 'en' : 'de'
  const { business, nerdy } = COPY[locale]
  const tick = Math.floor(options.elapsed / ROTATE_EVERY_S) + (options.seed ?? 0)
  const messages = phase?.messages ?? 0
  const longHistory = messages >= LONG_HISTORY_MESSAGES
  const attempt = phase?.attempt ?? 1
  const maxAttempts = phase?.maxAttempts ?? attempt

  let line: string

  switch (phase?.name) {
    case 'compressing':
      line = options.nerdy ? pick(nerdy.compressing, tick) : business.compressing()

      break

    case 'switching_model':
      line = options.nerdy ? pick(nerdy.switchingModel, tick) : business.switchingModel()

      break

    case 'retrying':
      line = options.nerdy ? pick(nerdy.retrying(phase.reason), tick) : business.retrying(phase.reason)

      break
    default:
      if (attempt > 1) {
        line = options.nerdy
          ? pick(nerdy.retryAttempt(phase?.retriedFor, attempt, maxAttempts), tick)
          : business.retryAttempt(phase?.retriedFor, attempt, maxAttempts)
      } else if (longHistory) {
        line = options.nerdy ? pick(nerdy.readingHistory(messages), tick) : business.readingHistory(messages)
      } else {
        line = options.nerdy ? pick(nerdy.thinking, tick) : business.thinking()
      }
  }

  const waiting = !phase || phase.name === 'waiting'

  if (!waiting || options.elapsed < SLOW_AFTER_S) {
    return { line }
  }

  const copy = options.nerdy ? nerdy : business

  return { hint: longHistory ? copy.slowLongHistory : copy.slow, line }
}
