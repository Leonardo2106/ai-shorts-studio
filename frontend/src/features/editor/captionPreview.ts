import type { CaptionCue, CaptionStyle } from '../../types/api'

export interface CaptionPreviewWord {
  startMs: number
  endMs: number
  text: string
  active: boolean
}

export interface CaptionPreview {
  kind: 'WORDS' | 'SEGMENT'
  words: CaptionPreviewWord[]
  text: string
}

export interface CaptionPreviewTimeline {
  at(previewMs: number): CaptionPreview | undefined
}

type CaptionTimingStyle = Pick<CaptionStyle, 'words_per_block' | 'gap_tolerance_ms' | 'min_display_ms' | 'hold_ms'>
interface TimelineEvent { startMs: number; endMs: number; preview: CaptionPreview }

const endsStrongSentence = (text: string) => /[.!?…]["'”’»)\]}]*$/u.test(text.trim())
const isWordCue = (cue: CaptionCue) => cue.words?.length === 1

/** Prepares sorting, ASS timing normalization and blocks once per cue/style change. */
export function createCaptionPreviewTimeline(
  cues: readonly CaptionCue[],
  style: CaptionTimingStyle,
  clipDurationMs: number,
): CaptionPreviewTimeline {
  const ordered = cues
    .map((cue, index) => ({ cue, index }))
    .sort((left, right) => left.cue.start_ms - right.cue.start_ms || left.cue.end_ms - right.cue.end_ms || left.index - right.index)
    .map(({ cue }) => cue)

  const blockByCue = new Map<CaptionCue, CaptionCue[]>()
  let current: CaptionCue[] = []
  const flush = () => {
    for (const cue of current) blockByCue.set(cue, current)
    current = []
  }
  for (const cue of ordered) {
    if (!isWordCue(cue)) {
      flush()
      continue
    }
    const previous = current.at(-1)
    const startsNewBlock = previous !== undefined && (
      current.length >= Math.max(1, style.words_per_block)
      || cue.start_ms - previous.end_ms > Math.max(0, style.gap_tolerance_ms)
      || endsStrongSentence(previous.text)
    )
    if (startsNewBlock) flush()
    current.push(cue)
  }
  flush()

  const events: TimelineEvent[] = ordered.flatMap((cue, index) => {
    const startMs = Math.max(0, Math.min(cue.start_ms, clipDurationMs))
    if (startMs >= clipDurationMs) return []
    const nextStartMs = index + 1 < ordered.length ? ordered[index + 1].start_ms : clipDurationMs
    const boundaryMs = Math.max(startMs, Math.min(nextStartMs, clipDurationMs))
    const naturalEndMs = Math.max(startMs, Math.min(cue.end_ms, clipDurationMs))
    const gapMs = nextStartMs - cue.end_ms
    const desiredEndMs = gapMs >= 0 && gapMs <= Math.max(0, style.gap_tolerance_ms)
      ? nextStartMs
      : naturalEndMs + Math.max(0, style.hold_ms)
    const endMs = Math.min(
      Math.max(desiredEndMs, startMs + Math.max(0, style.min_display_ms)),
      boundaryMs,
      clipDurationMs,
    )
    if (endMs <= startMs) return []
    const block = blockByCue.get(cue)
    const preview: CaptionPreview = block ? {
      kind: 'WORDS',
      text: block.map((wordCue) => wordCue.text).join(' '),
      words: block.map((wordCue) => ({
        startMs: wordCue.start_ms,
        endMs: wordCue.end_ms,
        text: wordCue.text,
        active: wordCue === cue,
      })),
    } : { kind: 'SEGMENT', words: [], text: cue.text }
    return [{ startMs, endMs, preview }]
  })

  return {
    at(previewMs) {
      let low = 0
      let high = events.length - 1
      let found = -1
      while (low <= high) {
        const middle = Math.floor((low + high) / 2)
        if (events[middle].startMs <= previewMs) {
          found = middle
          low = middle + 1
        } else high = middle - 1
      }
      const event = found >= 0 ? events[found] : undefined
      return event && previewMs < event.endMs ? event.preview : undefined
    },
  }
}

/** Convenience API for isolated lookups; interactive consumers should prepare a timeline once. */
export function captionPreviewAt(cues: readonly CaptionCue[], style: CaptionTimingStyle, previewMs: number, clipDurationMs: number) {
  return createCaptionPreviewTimeline(cues, style, clipDurationMs).at(previewMs)
}
