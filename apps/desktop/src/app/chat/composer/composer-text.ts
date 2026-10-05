// assistant-ui throws "Composer is not available" while a thread's composer
// core is not mounted yet — e.g. right after a session switch remounts the
// runtime. Programmatic writes (draft restore, inserts, clears) used to throw
// uncaught there (SUP-20261005-103125). They now wait a few frames for the
// core; the newest text per composer wins.

export const COMPOSER_UNAVAILABLE = 'Composer is not available'

const MAX_ATTEMPTS = 30

interface ComposerTextTarget {
  composer(): { setText(text: string): void }
}

type Schedule = (run: () => void) => void

const pending = new WeakMap<ComposerTextTarget, string>()

const nextFrame: Schedule = run => {
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => run())
  } else {
    setTimeout(run, 16)
  }
}

export function isComposerUnavailable(error: unknown): boolean {
  return error instanceof Error && error.message === COMPOSER_UNAVAILABLE
}

export function setComposerText(aui: ComposerTextTarget, text: string, schedule: Schedule = nextFrame): void {
  const retrying = pending.has(aui)
  pending.set(aui, text)

  if (retrying) {
    return
  }

  let attempts = 0

  const attempt = () => {
    const latest = pending.get(aui) ?? text

    try {
      aui.composer().setText(latest)
      pending.delete(aui)
    } catch (error) {
      if (!isComposerUnavailable(error)) {
        pending.delete(aui)
        throw error
      }

      attempts += 1

      if (attempts >= MAX_ATTEMPTS) {
        pending.delete(aui)

        return
      }

      schedule(attempt)
    }
  }

  attempt()
}
