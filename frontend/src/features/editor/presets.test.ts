import { describe, expect, it } from 'vitest'
import { CAPTION_PRESETS, createEditConfig, EDITOR_PRESETS, normalizeEditConfig } from './presets'

describe('editor presets', () => {
  it('defines four serializable 1080x1920 layouts', () => {
    expect(EDITOR_PRESETS).toHaveLength(4)
    for (const preset of EDITOR_PRESETS) {
      const config = createEditConfig(preset)
      expect(config).toMatchObject({ schema_version: 2, audio: { mode: 'TRANSCRIPT_DEFAULT', tracks: [] } })
      expect([config.canvas_width, config.canvas_height]).toEqual([1080, 1920])
      expect(config.elements.some((element) => element.kind === 'SCREEN')).toBe(true)
      expect(config.elements.some((element) => element.kind === 'WEBCAM')).toBe(true)
      expect(config.elements.every((element) => element.radius === 0 && element.padding === 0)).toBe(true)
      expect(config.elements.every((element) => element.zoom === 1 && element.focal_x === .5 && element.focal_y === .5)).toBe(true)
      expect(JSON.parse(JSON.stringify(config))).toEqual(config)
    }
  })

  it('preserves the literal Roadmap 01 contain layouts and provides concrete caption macros', () => {
    const base = createEditConfig(EDITOR_PRESETS[0])
    expect(base.elements.find((item) => item.kind === 'CAPTIONS')).toMatchObject({ x: 60, y: 1320, width: 960, height: 300, z_index: 5 })
    expect(base.elements.find((item) => item.kind === 'BANNER')).toMatchObject({ y: 1680, height: 240, z_index: 3 })
    expect(base.elements.find((item) => item.kind === 'SCREEN')).toMatchObject({ fit: 'CONTAIN' })
    expect(createEditConfig(EDITOR_PRESETS[3]).elements.find((item) => item.kind === 'SCREEN')).toMatchObject({ fit: 'CONTAIN' })
    expect(CAPTION_PRESETS.BOLD.style).toMatchObject({ weight: 900, uppercase: true })
    expect(CAPTION_PRESETS.GAMING.style).toMatchObject({ italic: true, box_color: '#000000', gap_tolerance_ms: 180 })
  })

  it('normalizes configs persisted before framing and pause controls existed', () => {
    const legacy = createEditConfig()
    delete (legacy.elements[0] as Partial<typeof legacy.elements[number]>).zoom
    delete (legacy.elements[0] as Partial<typeof legacy.elements[number]>).focal_x
    delete (legacy.captions as Partial<typeof legacy.captions>).outline_color
    delete (legacy.captions as Partial<typeof legacy.captions>).hold_ms
    legacy.captions.font_family = 'Inter'
    const normalized = normalizeEditConfig(legacy)
    expect(normalized.elements[0]).toMatchObject({ zoom: 1, focal_x: .5, focal_y: .5 })
    expect(normalized.captions).toMatchObject({ font_family: 'Inter', outline_color: '#000000', hold_ms: 150 })
  })

  it('migrates v1 configs to transcript-default audio locally', () => {
    const legacy = { ...createEditConfig(), schema_version: 1 as const }
    delete (legacy as Partial<typeof legacy>).audio

    expect(normalizeEditConfig(legacy as unknown as Parameters<typeof normalizeEditConfig>[0])).toMatchObject({ schema_version: 2, audio: { mode: 'TRANSCRIPT_DEFAULT', tracks: [] } })
  })
})
