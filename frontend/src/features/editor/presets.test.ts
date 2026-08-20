import { describe, expect, it } from 'vitest'
import { createEditConfig, EDITOR_PRESETS } from './presets'

describe('editor presets', () => {
  it('defines four serializable 1080x1920 layouts', () => {
    expect(EDITOR_PRESETS).toHaveLength(4)
    for (const preset of EDITOR_PRESETS) {
      const config = createEditConfig(preset)
      expect([config.canvas_width, config.canvas_height]).toEqual([1080, 1920])
      expect(config.elements.some((element) => element.kind === 'SCREEN')).toBe(true)
      expect(config.elements.some((element) => element.kind === 'WEBCAM')).toBe(true)
      expect(JSON.parse(JSON.stringify(config))).toEqual(config)
    }
  })
})
