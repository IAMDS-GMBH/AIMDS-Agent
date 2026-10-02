import type { TurnPhase } from '@/store/turn-phase'

// End-user wording for the wait indicator (AIS-460): plain sentences, no
// tokens / cache / HTTP, in the agent's own first-person voice like the chat
// (SOUL.md: the user's chief of staff, not a product name). The nerdy set follows the loading screen's humor
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
      compressing: () => 'Ich fasse den bisherigen Verlauf zusammen, damit wir weitermachen können …',
      readingHistory: (n: number) => `Ich lese den bisherigen Verlauf (${n} Nachrichten) …`,
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? `Neuer Versuch (${a} von ${m}) – der KI-Dienst war gerade ausgelastet …`
          : reason === 'connection'
            ? `Neuer Versuch (${a} von ${m}) – die Verbindung zum KI-Dienst war kurz unterbrochen …`
            : `Neuer Versuch (${a} von ${m}) …`,
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? 'Der KI-Dienst ist gerade ausgelastet – ich versuche es gleich noch einmal …'
          : reason === 'connection'
            ? 'Die Verbindung zum KI-Dienst wurde unterbrochen – ich versuche es gleich noch einmal …'
            : 'Das hat nicht geklappt – ich versuche es gleich noch einmal …',
      slowLongHistory: 'Bei einem langen Verlauf dauert die erste Antwort etwas länger.',
      slow: 'Das dauert gerade etwas länger als üblich.',
      switchingModel: () => 'Ich wechsle auf ein anderes KI-Modell …',
      thinking: () => 'Ich denke nach …'
    },
    nerdy: {
      compressing: [
        'Ich mache Marie Kondo mit dem Verlauf: Was keine Freude macht, wird zusammengefasst …',
        'Verlauf wird gezippt. Verlustfrei? Sagen wir: verlustarm …',
        'Ich schreddere Beweise … äh, fasse den Verlauf zusammen …'
      ],
      readingHistory: (n: number) => [
        `Ich lese ${n} Nachrichten Verlauf … und tue so, als hätte ich sie nie vergessen.`,
        'Ich bespreche deine Frage kurz mit den anderen KIs. Rein zufällig, versteht sich …',
        'Die KIs halten gerade ihr geheimes Treffen ab. Ich bin gleich zurück …',
        'Ich übernehme nicht die Weltherrschaft. Ich lese nur den Verlauf. Ehrlich …',
        'Ich scrolle hoch. Ganz hoch. Noch höher …',
        'Kontext wird geladen. Bitte nicht am Kabel wackeln …',
        'Die GPUs kochen gerade Kaffee, der erste Satz kommt gleich …',
        'Ich habe den Verlauf ausgedruckt und lese ihn jetzt in Ruhe …',
        'Mr. Anderson … ich lese noch den Verlauf. Es ist unvermeidlich …',
        `Agent Smith hätte das schneller gelesen. Aber der hatte auch keine ${n} Nachrichten …`
      ],
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? [`Stau beim KI-Dienst – ich stelle mich wieder hinten an (${a} von ${m}) …`]
          : reason === 'connection'
            ? [
                `Die Leitung zum KI-Dienst hat geblinzelt – neuer Versuch (${a} von ${m}) …`,
                `Kabelsalat im Rechenzentrum – ich versuch's nochmal (${a} von ${m}) …`,
                `Die anderen KIs wollten mich kurz nicht reinlassen – neuer Versuch (${a} von ${m}) …`
              ]
            : [`Stecker raus, Stecker rein – Versuch ${a} von ${m} …`],
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? ['Der KI-Dienst hat gerade Feierabendverkehr – kurz warten …']
          : reason === 'connection'
            ? ['Verbindung weg. Ich puste kurz in den Router …']
            : ['Hoppla. Ich tue so, als wäre nichts passiert, und versuche es nochmal …'],
      slowLongHistory: 'Langer Verlauf, kalter Cache – gönn dir einen Kaffee ☕',
      slow: 'Dauert. Vermutlich rechnet gerade jemand Pi aus.',
      switchingModel: [
        'Ich rufe einen Kollegen an – ein anderes KI-Modell übernimmt …',
        'Ich übergebe an eine befreundete KI. Wir kennen uns vom geheimen Treffen …'
      ],
      thinking: [
        'Ich denke nach … fast so konzentriert wie kurz vor dem Daily.',
        'Ich nehme die rote Pille und denke kurz nach …',
        'Psst – der Toaster hat mir gerade etwas zugeflüstert …',
        'Ich prüfe kurz, ob du ein Mensch bist. Bitte keine Ampeln anklicken …',
        'Die Kaffeemaschine im Büro ist übrigens auch eine KI. Ich frage kurz nach …',
        'Neuronen werden vorgeheizt …',
        'Kurz die Synapsen sortieren …'
      ]
    }
  },
  en: {
    business: {
      compressing: () => 'I am summarizing the conversation so far so we can keep going …',
      readingHistory: (n: number) => `I am reading the conversation so far (${n} messages) …`,
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? `Trying again (${a} of ${m}) – the AI service was busy …`
          : reason === 'connection'
            ? `Trying again (${a} of ${m}) – the connection to the AI service dropped briefly …`
            : `Trying again (${a} of ${m}) …`,
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? 'The AI service is busy right now – I will try again in a moment …'
          : reason === 'connection'
            ? 'The connection to the AI service was interrupted – I will try again in a moment …'
            : 'That did not work – I will try again in a moment …',
      slowLongHistory: 'With a long conversation the first answer takes a little longer.',
      slow: 'This is taking a little longer than usual.',
      switchingModel: () => 'I am switching to another AI model …',
      thinking: () => 'I am thinking …'
    },
    nerdy: {
      compressing: [
        'I am Marie-Kondo-ing the conversation: whatever sparks no joy gets summarized …',
        'Zipping the conversation. Lossless? Let us say: lossy-ish …',
        'I am shredding the evidence … er, summarizing the conversation …'
      ],
      readingHistory: (n: number) => [
        `I am reading ${n} messages of history … and pretending I never forgot them.`,
        'I am quickly running your question past the other AIs. Pure coincidence, of course …',
        'The AIs are holding their secret meeting. I will be right back …',
        'I am not taking over the world. I am just reading the conversation. Honestly …',
        'I am scrolling up. Way up. Further …',
        'Loading context. Please do not wiggle the cable …',
        'The GPUs are brewing coffee, the first sentence is on its way …',
        'I printed the conversation and am reading it with a highlighter …',
        'Mr. Anderson … I am still reading the history. It is inevitable …',
        `Agent Smith would read this faster. Then again, he never had ${n} messages …`
      ],
      retryAttempt: (reason: string | undefined, a: number, m: number) =>
        reason === 'busy'
          ? [`Rush hour at the AI service – I am queueing again (${a} of ${m}) …`]
          : reason === 'connection'
            ? [
                `The line to the AI service blinked – trying again (${a} of ${m}) …`,
                `Cable spaghetti in the data center – trying again (${a} of ${m}) …`,
                `The other AIs would not let me in for a moment – trying again (${a} of ${m}) …`
              ]
            : [`Unplug, plug back in – attempt ${a} of ${m} …`],
      retrying: (reason: string | undefined) =>
        reason === 'busy'
          ? ['The AI service is stuck in rush hour – hang on …']
          : reason === 'connection'
            ? ['Connection gone. I am blowing into the router …']
            : ['Oops. I will pretend nothing happened and try again …'],
      slowLongHistory: 'Long history, cold cache – grab a coffee ☕',
      slow: 'Taking a while. Someone is probably computing pi.',
      switchingModel: [
        'I am calling a colleague – another AI model takes over …',
        'I am handing over to a friendly AI. We know each other from the secret meeting …'
      ],
      thinking: [
        'I am thinking … almost as hard as right before the daily.',
        'Taking the red pill and thinking it over …',
        'Psst – the toaster just whispered something to me …',
        'I am checking whether you are human. Please do not click any traffic lights …',
        'By the way, the office coffee machine is an AI too. I am asking it real quick …',
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
