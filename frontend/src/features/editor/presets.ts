import type { CaptionStyle, EditConfig, EditorElement, LayoutPreset } from '../../types/api'

export const CANVAS = { width: 1080 as const, height: 1920 as const }

const element = (value: Partial<EditorElement> & Pick<EditorElement, 'id' | 'kind' | 'x' | 'y' | 'width' | 'height'>): EditorElement => ({ z_index: 0, visible: true, fit: 'COVER', opacity: 1, border_width: 0, border_color: '#000000', radius: 0, padding: 0, zoom: 1, focal_x: .5, focal_y: .5, ...value })
const captions = () => element({ id: 'captions', kind: 'CAPTIONS', x: 60, y: 1320, width: 960, height: 300, z_index: 5 })
const banner = (visible = false) => element({ id: 'banner', kind: 'BANNER', x: 0, y: 1680, width: 1080, height: 240, visible, z_index: 3 })

export const CAPTION_DEFAULTS: CaptionStyle = { enabled: true, font_family: 'Arial', font_size: 64, color: '#FFFFFF', weight: 700, uppercase: false, outline_width: 4, outline_color: '#000000', shadow: true, italic: false, box_color: null, max_width: 960, words_per_line: 5, words_per_block: 10, active_word_color: '#FFE600', gap_tolerance_ms: 250, min_display_ms: 300, hold_ms: 150 }
export const PORTABLE_CAPTION_FONTS = ['Arial', 'Verdana', 'Tahoma', 'Trebuchet MS', 'sans-serif'] as const
export const CAPTION_PRESETS = {
  CLEAN: { label: 'Clean', style: { ...CAPTION_DEFAULTS } },
  BOLD: { label: 'Bold', style: { ...CAPTION_DEFAULTS, weight: 900, font_size: 72, uppercase: true, words_per_line: 4, active_word_color: '#38BDF8' } },
  GAMING: { label: 'Gaming', style: { ...CAPTION_DEFAULTS, weight: 900, italic: true, uppercase: true, font_size: 76, outline_width: 6, box_color: '#000000', active_word_color: '#22D3EE', gap_tolerance_ms: 180, hold_ms: 100 } },
} satisfies Record<string, { label: string; style: CaptionStyle }>
export type CaptionPresetId = keyof typeof CAPTION_PRESETS | 'CUSTOM'

export const EDITOR_PRESETS = [
  { id: 'WEBCAM_TOP_SCREEN_BOTTOM', label: 'Webcam em cima + tela embaixo', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 960 }), element({ id: 'screen', kind: 'SCREEN', x: 0, y: 960, width: 1080, height: 960, fit: 'CONTAIN' }), captions(), banner()] },
  { id: 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM', label: 'Webcam + tela + banner', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 720 }), element({ id: 'screen', kind: 'SCREEN', x: 0, y: 720, width: 1080, height: 960, fit: 'CONTAIN' }), captions(), banner(true)] },
  { id: 'SCREEN_FULLSCREEN_WEBCAM_OVERLAY', label: 'Tela cheia + webcam sobreposta', build: (): EditorElement[] => [element({ id: 'screen', kind: 'SCREEN', x: 0, y: 0, width: 1080, height: 1920 }), element({ id: 'webcam', kind: 'WEBCAM', x: 700, y: 80, width: 320, height: 480, z_index: 2 }), captions(), banner()] },
  { id: 'WEBCAM_FULLSCREEN_SCREEN_PIP', label: 'Webcam cheia + tela picture-in-picture', build: (): EditorElement[] => [element({ id: 'webcam', kind: 'WEBCAM', x: 0, y: 0, width: 1080, height: 1920 }), element({ id: 'screen', kind: 'SCREEN', x: 80, y: 1300, width: 920, height: 520, z_index: 2, fit: 'CONTAIN' }), captions(), banner()] },
] as const

type EditorPreset = (typeof EDITOR_PRESETS)[number]

export function createEditConfig(preset: EditorPreset = EDITOR_PRESETS[0]): EditConfig {
  const showBanner = preset.id === 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM'
  return { schema_version: 2, canvas_width: 1080, canvas_height: 1920, preset: preset.id as LayoutPreset, elements: preset.build(), captions: { ...CAPTION_DEFAULTS }, banner: { enabled: showBanner, text: 'Seu título aqui', image_relative_path: null, background_color: '#0F172A', opacity: .9, start_ms: 0, end_ms: null }, audio: { mode: 'TRANSCRIPT_DEFAULT', tracks: [] }, background_color: '#080C14' }
}

type LegacyEditConfig = Omit<EditConfig, 'schema_version' | 'audio'> & { schema_version: 1; audio?: undefined }

export function normalizeEditConfig(config: EditConfig | LegacyEditConfig): EditConfig {
  const captions = { ...CAPTION_DEFAULTS, ...config.captions }
  return {
    ...config,
    schema_version: 2,
    elements: config.elements.map((item) => { const media = item.kind === 'SCREEN' || item.kind === 'WEBCAM'; const contain = item.fit === 'CONTAIN'; const zoom = media ? item.zoom ?? 1 : 1; return { ...item, zoom: contain && zoom > 1 ? 1 : zoom, focal_x: media && !contain ? item.focal_x ?? .5 : .5, focal_y: media && !contain ? item.focal_y ?? .5 : .5 } }),
    captions,
    audio: config.audio ?? { mode: 'TRANSCRIPT_DEFAULT', tracks: [] },
  }
}
