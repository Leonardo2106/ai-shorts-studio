import type { EditConfig, EditorElement, LayoutPreset } from '../../types/api'

export const CANVAS = { width: 1080 as const, height: 1920 as const }

const element = (value: Partial<EditorElement> & Pick<EditorElement, 'id' | 'kind' | 'x' | 'y' | 'width' | 'height'>): EditorElement => ({ z_index: 0, visible: true, fit: 'COVER', opacity: 1, border_width: 0, border_color: '#000000', radius: 0, padding: 0, ...value })
const captions = () => element({ id: 'captions', kind: 'CAPTIONS', x: 60, y: 1390, width: 960, height: 300, z_index: 20, padding: 24, radius: 16 })
const banner = (visible = false) => element({ id: 'banner', kind: 'BANNER', x: 0, y: 1680, width: 1080, height: 240, visible, z_index: 10, opacity: .95, radius: 24, padding: 28 })

export const EDITOR_PRESETS = [
  { id: 'WEBCAM_TOP_SCREEN_BOTTOM', label: 'Webcam em cima + tela embaixo', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 960 }), element({ id: 'screen', kind: 'SCREEN', x: 0, y: 960, width: 1080, height: 960, fit: 'CONTAIN' }), captions(), banner()] },
  { id: 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM', label: 'Webcam + tela + banner', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 720 }), element({ id: 'screen', kind: 'SCREEN', x: 0, y: 720, width: 1080, height: 960, fit: 'CONTAIN' }), captions(), banner(true)] },
  { id: 'SCREEN_FULLSCREEN_WEBCAM_OVERLAY', label: 'Tela cheia + webcam sobreposta', build: (): EditorElement[] => [element({ id: 'screen', kind: 'SCREEN', x: 0, y: 0, width: 1080, height: 1920 }), element({ id: 'webcam', kind: 'WEBCAM', x: 700, y: 80, width: 320, height: 480, z_index: 2, radius: 36, border_width: 6, border_color: '#FFFFFF' }), captions(), banner()] },
  { id: 'WEBCAM_FULLSCREEN_SCREEN_PIP', label: 'Webcam cheia + tela picture-in-picture', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 1920 }), element({ id: 'screen', kind: 'SCREEN', x: 80, y: 1300, width: 920, height: 520, z_index: 2, fit: 'CONTAIN', radius: 28, border_width: 6, border_color: '#FFFFFF' }), captions(), banner()] },
] as const

type EditorPreset = (typeof EDITOR_PRESETS)[number]

export function createEditConfig(preset: EditorPreset = EDITOR_PRESETS[0]): EditConfig {
  const showBanner = preset.id === 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM'
  return { schema_version: 1, canvas_width: 1080, canvas_height: 1920, preset: preset.id as LayoutPreset, elements: preset.build(), captions: { enabled: true, font_family: 'Inter', font_size: 64, color: '#FFFFFF', weight: 800, uppercase: false, outline_width: 4, shadow: true, box_color: '#000000', max_width: 960, words_per_line: 5, words_per_block: 10, active_word_color: '#38BDF8' }, banner: { enabled: showBanner, text: 'Seu título aqui', image_relative_path: null, background_color: '#0F172A', opacity: .9, start_ms: 0, end_ms: null }, background_color: '#080C14' }
}
