import { describe, expect, it } from 'vitest'
import type { CaptionCue } from '../../types/api'
import { captionPreviewAt, createCaptionPreviewTimeline } from './captionPreview'

const word = (text: string, start_ms: number, end_ms: number): CaptionCue => ({
  start_ms,
  end_ms,
  text,
  words: [{ start_ms, end_ms, text }],
})
const style = { words_per_block: 3, gap_tolerance_ms: 250, min_display_ms: 0, hold_ms: 0 }
const clipDurationMs = 2000

describe('captionPreviewAt', () => {
  it('prepares one timeline for repeated instant lookups', () => {
    const timeline = createCaptionPreviewTimeline(
      [word('primeira', 0, 100), word('segunda', 100, 200)],
      style,
      clipDurationMs,
    )

    expect(timeline.at(50)?.words.find((item) => item.active)?.text).toBe('primeira')
    expect(timeline.at(150)?.words.find((item) => item.active)?.text).toBe('segunda')
    expect(timeline.at(1000)).toBeUndefined()
  })

  it('shows the active word inside its automatic size-limited block', () => {
    const cues = [word('um', 0, 100), word('dois', 100, 200), word('três', 200, 300), word('quatro', 300, 400)]

    expect(captionPreviewAt(cues, style, 150, clipDurationMs)).toMatchObject({
      kind: 'WORDS',
      text: 'um dois três',
      words: [{ text: 'um', active: false }, { text: 'dois', active: true }, { text: 'três', active: false }],
    })
    expect(captionPreviewAt(cues, style, 350, clipDurationMs)?.text).toBe('quatro')
  })

  it('starts a new block after a long pause or strong terminal punctuation', () => {
    const cues = [
      word('Olá!', 0, 100),
      word('Novo', 110, 200),
      word('bloco', 210, 300),
      word('Depois', 600, 700),
    ]

    const blockStyle = { ...style, words_per_block: 10 }
    expect(captionPreviewAt(cues, blockStyle, 50, clipDurationMs)?.text).toBe('Olá!')
    expect(captionPreviewAt(cues, blockStyle, 150, clipDurationMs)?.text).toBe('Novo bloco')
    expect(captionPreviewAt(cues, blockStyle, 650, clipDurationMs)?.text).toBe('Depois')
  })

  it('keeps segment fallback intact and does not invent an active word', () => {
    const segment: CaptionCue = { start_ms: 0, end_ms: 1000, text: 'fallback por segmento', words: null }

    expect(captionPreviewAt([segment], style, 500, clipDurationMs)).toEqual({
      kind: 'SEGMENT',
      text: 'fallback por segmento',
      words: [],
    })
  })

  it('returns no caption outside cue timing', () => {
    expect(captionPreviewAt([word('agora', 100, 200)], style, 200, clipDurationMs)).toBeUndefined()
  })

  it('keeps the current event visible across a short tolerated gap', () => {
    const cues = [word('um', 0, 100), word('dois', 300, 400)]

    const preview = captionPreviewAt(cues, style, 250, clipDurationMs)

    expect(preview?.text).toBe('um dois')
    expect(preview?.words.find((item) => item.active)?.text).toBe('um')
  })

  it('applies minimum display and hold without crossing the next event', () => {
    const timingStyle = { ...style, gap_tolerance_ms: 50, min_display_ms: 300, hold_ms: 0 }
    const cues = [word('curta', 0, 50), word('depois', 500, 600)]

    expect(captionPreviewAt(cues, timingStyle, 299, clipDurationMs)?.words.find((item) => item.active)?.text).toBe('curta')
    expect(captionPreviewAt(cues, timingStyle, 300, clipDurationMs)).toBeUndefined()
    expect(captionPreviewAt(cues, timingStyle, 499, clipDurationMs)).toBeUndefined()
    expect(captionPreviewAt(cues, timingStyle, 500, clipDurationMs)?.words.find((item) => item.active)?.text).toBe('depois')

    const holdStyle = { ...style, gap_tolerance_ms: 50, min_display_ms: 0, hold_ms: 150 }
    expect(captionPreviewAt([word('hold', 0, 50)], holdStyle, 199, 1000)?.text).toBe('hold')
    expect(captionPreviewAt([word('hold', 0, 50)], holdStyle, 200, 1000)).toBeUndefined()
  })

  it('clips normalized caption timing at the candidate boundary', () => {
    const timingStyle = { ...style, gap_tolerance_ms: 0, min_display_ms: 500, hold_ms: 500 }
    const cues = [word('fim', 900, 950)]

    expect(captionPreviewAt(cues, timingStyle, 999, 1000)?.text).toBe('fim')
    expect(captionPreviewAt(cues, timingStyle, 1000, 1000)).toBeUndefined()
  })
})
