import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api/client'
import type { Candidate, EditConfigResponse, MediaAsset, Transcript } from '../../types/api'
import { EditorPanel } from './EditorPanel'
import { createEditConfig, EDITOR_PRESETS } from './presets'

const candidate: Candidate = { id: 'candidate-1', project_id: 'project-1', transcript_id: 'transcript-1', schema_version: 1, start_ms: 1000, end_ms: 31000, title: 'Clip', status: 'ACCEPTED', text: 'Trecho', origin: 'LOCAL', local_features: {}, reasons: [], context: {}, signals: {}, score: 2, score_breakdown: [], created_at: '', updated_at: '' }
const transcript: Transcript = { id: 'transcript-1', project_id: 'project-1', media_id: 'screen-1', source: 'SCREEN', audio_stream_index: 1, language: 'pt', duration_ms: 30000, engine: 'whisper', model: 'tiny', segments: [] }
const media: MediaAsset[] = [{ id: 'screen-1', role: 'SCREEN', original_filename: 'screen.mp4', size_bytes: 1, sha256: 'a', content_url: '/media', probe: { duration_ms: 30000, format_name: 'mp4', bitrate: null, video_streams: [], audio_streams: [{ index: 1, codec_name: 'aac', sample_rate: 48000, channels: 2, channel_layout: 'stereo', language: 'pt' }, { index: 2, codec_name: 'opus', sample_rate: 48000, channels: 1, channel_layout: 'mono', language: null, metadata: { title: 'Desktop Audio' } }] } }]

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} onClose={() => undefined} /></QueryClientProvider>)
}

afterEach(() => vi.restoreAllMocks())

function mockRenderingRecovery() {
  vi.spyOn(api, 'listRenderJobs').mockResolvedValue([])
  vi.spyOn(api, 'listRenderArtifacts').mockResolvedValue([])
}

function mockBaseEditor() {
  vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
  vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS', items: [] })
  mockRenderingRecovery()
  return vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' } satisfies EditConfigResponse))
}

describe('EditorPanel', () => {
  it('initializes custom audio from real streams and preserves the transcript track', async () => {
    const save = mockBaseEditor()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const user = userEvent.setup()
    render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} media={media} transcript={transcript} onClose={() => undefined} /></QueryClientProvider>)
    await screen.findByText('Timing: palavras.')

    await user.selectOptions(screen.getByLabelText('Fonte do áudio'), 'CUSTOM')
    const tracks = screen.getAllByRole('checkbox', { name: /Tela · screen\.mp4 · Track/ })
    expect(tracks[0]).toBeChecked()
    expect(tracks[1]).not.toBeChecked()
    expect(screen.getByText(/Track 1 · aac · 2 canais · 48000 Hz · pt/)).toBeInTheDocument()
    fireEvent.change(screen.getAllByLabelText('Ganho (dB)')[0], { target: { value: '24' } })
    expect(screen.getAllByLabelText('Ganho (dB)')[0]).toHaveValue(12)
    expect(screen.getByText(/Track 2 · Desktop Audio · opus/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Fonte do áudio'), 'TRANSCRIPT_DEFAULT')
    await user.selectOptions(screen.getByLabelText('Fonte do áudio'), 'CUSTOM')
    expect(screen.getAllByLabelText('Ganho (dB)')[0]).toHaveValue(12)
    expect(screen.getAllByRole('checkbox', { name: /Tela · screen\.mp4 · Track/ })[0]).toBeChecked()
    await user.selectOptions(screen.getByLabelText('Preset de layout'), 'SCREEN_FULLSCREEN_WEBCAM_OVERLAY')
    expect(screen.getByLabelText('Fonte do áudio')).toHaveValue('CUSTOM')
    expect(tracks[0]).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][2].audio.tracks[0]).toMatchObject({ gain_db: 12, enabled: true })
    await user.click(screen.getAllByRole('checkbox', { name: /Tela · screen\.mp4 · Track/ })[0])
    expect(screen.getByText('Render sem áudio')).toBeInTheDocument()
  })

  it('prevents enabling a ninth custom audio track and explains the limit', async () => {
    mockBaseEditor()
    const manyStreams: MediaAsset[] = [{
      ...media[0],
      probe: {
        ...media[0].probe,
        audio_streams: Array.from({ length: 9 }, (_, index) => ({ index: index + 1, codec_name: 'aac', sample_rate: 48000, channels: 2, channel_layout: 'stereo', language: 'pt' })),
      },
    }]
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const user = userEvent.setup()
    render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} media={manyStreams} transcript={transcript} onClose={() => undefined} /></QueryClientProvider>)
    await screen.findByText('Timing: palavras.')
    await user.selectOptions(screen.getByLabelText('Fonte do áudio'), 'CUSTOM')
    const tracks = screen.getAllByRole('checkbox', { name: /Tela · screen\.mp4 · Track/ })

    for (const track of tracks.slice(1, 8)) await user.click(track)

    expect(screen.getByText('Limite de 8 tracks ativas')).toBeInTheDocument()
    expect(tracks[0]).toBeEnabled()
    expect(tracks[8]).toBeDisabled()
    await user.click(tracks[7])
    expect(screen.queryByText('Limite de 8 tracks ativas')).not.toBeInTheDocument()
    expect(tracks[8]).toBeEnabled()
  })

  it('keeps caption controls accessible but hides caption content when captions are disabled', async () => {
    const config = createEditConfig()
    config.captions.enabled = false
    vi.spyOn(api, 'getEditConfig').mockResolvedValue({ id: 'edit-1', project_id: 'project-1', candidate_id: candidate.id, schema_version: 1, config, created_at: '', updated_at: '' })
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS', items: [{ start_ms: 0, end_ms: 1000, text: 'não mostrar', words: [{ start_ms: 0, end_ms: 1000, text: 'não mostrar' }] }] })
    mockRenderingRecovery()
    vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, nextConfig) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config: nextConfig, created_at: '', updated_at: '' }))

    const user = userEvent.setup()
    renderEditor()
    await screen.findByText('Timing: palavras.')

    expect(screen.queryByText('não mostrar')).not.toBeInTheDocument()
    expect(screen.queryByText('Sem legenda neste instante')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'CAPTIONS' }))
    expect(screen.getByLabelText('Legendas ativas')).not.toBeChecked()
  })

  it('does not show an empty-caption placeholder while cues are loading', async () => {
    let resolveCues!: (value: Awaited<ReturnType<typeof api.getCandidateCaptions>>) => void
    vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
    vi.spyOn(api, 'getCandidateCaptions').mockImplementation(() => new Promise((resolve) => { resolveCues = resolve }))
    mockRenderingRecovery()
    vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' }))

    renderEditor()
    expect(await screen.findByText('Carregando legendas automáticas…')).toBeInTheDocument()
    expect(screen.queryByText('Sem legenda neste instante')).not.toBeInTheDocument()
    await act(async () => resolveCues({ timing_source: 'WORDS', items: [] }))
    expect(await screen.findByText('Sem legenda neste instante')).toBeInTheDocument()
  })

  it('shows cue loading errors separately and retries them', async () => {
    vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
    const captions = vi.spyOn(api, 'getCandidateCaptions')
      .mockRejectedValueOnce(new ApiError(500, 'CAPTIONS_UNAVAILABLE', 'indisponível'))
      .mockResolvedValueOnce({ timing_source: 'SEGMENTS', items: [] })
    mockRenderingRecovery()
    vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' }))
    const user = userEvent.setup()

    renderEditor()
    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível carregar as legendas')
    expect(screen.queryByText('Sem legenda neste instante')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tentar carregar legendas novamente' }))

    expect(await screen.findByText('Timing: segmentos.')).toBeInTheDocument()
    expect(screen.getByText('Sem legenda neste instante')).toBeInTheDocument()
    expect(captions).toHaveBeenCalledTimes(2)
  })

  it('previews the automatic caption block and highlights only its active word', async () => {
    vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({
      timing_source: 'WORDS',
      items: [
        { start_ms: 0, end_ms: 100, text: 'um', words: [{ start_ms: 0, end_ms: 100, text: 'um' }] },
        { start_ms: 100, end_ms: 200, text: 'dois', words: [{ start_ms: 100, end_ms: 200, text: 'dois' }] },
        { start_ms: 200, end_ms: 300, text: 'três', words: [{ start_ms: 200, end_ms: 300, text: 'três' }] },
      ],
    })
    mockRenderingRecovery()
    vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' }))

    renderEditor()
    await screen.findByText('Timing: palavras.')
    fireEvent.change(screen.getByLabelText(/Instante do preview/), { target: { value: '150' } })

    expect(screen.getByText('um')).toHaveStyle({ color: '#FFFFFF' })
    expect(screen.getByText('dois')).toHaveAttribute('data-caption-active', 'true')
    expect(screen.getByText('dois')).toHaveStyle({ color: '#FFE600' })
    expect(screen.getByText('três')).toHaveStyle({ color: '#FFFFFF' })
  })

  it('applies presets, edits captions and persists a versioned config', async () => {
    vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS_AND_SEGMENTS', items: [{ start_ms: 0, end_ms: 1000, text: 'Olá', words: [{ start_ms: 0, end_ms: 1000, text: 'Olá' }] }, { start_ms: 1000, end_ms: 3000, text: 'fallback por segmento', words: null }] })
    mockRenderingRecovery()
    const save = vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' } satisfies EditConfigResponse))
    const user = userEvent.setup()
    renderEditor()
    expect(await screen.findByText('Timing: palavras quando disponíveis, com fallback por segmento.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Preset de layout'), 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM')
    await user.click(screen.getByRole('button', { name: 'CAPTIONS' }))
    fireEvent.change(screen.getByLabelText('Tamanho da fonte'), { target: { value: '72' } })
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][2]).toMatchObject({ schema_version: 2, canvas_width: 1080, canvas_height: 1920, preset: 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM', captions: { font_size: 72 }, banner: { enabled: true }, audio: { mode: 'TRANSCRIPT_DEFAULT' } })
  })

  it('applies caption macros and persists background, font, outline and pause controls', async () => {
    const save = mockBaseEditor()
    const user = userEvent.setup()
    renderEditor()
    await user.click(await screen.findByRole('button', { name: 'CAPTIONS' }))
    await user.selectOptions(screen.getByLabelText('Estilo de legenda'), 'GAMING')
    expect(screen.getByLabelText('Fundo da legenda')).toBeChecked()
    await user.click(screen.getByLabelText('Fundo da legenda'))
    expect(screen.queryByLabelText('Cor do fundo')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: /^Família da fonte/ }), 'Verdana')
    await user.selectOptions(screen.getByLabelText('Peso'), '400')
    fireEvent.change(screen.getByLabelText('Cor do contorno'), { target: { value: '#112233' } })
    fireEvent.change(screen.getByLabelText('Unir pausas até (ms)'), { target: { value: '420' } })
    fireEvent.change(screen.getByLabelText('Exibição mínima (ms)'), { target: { value: '550' } })
    fireEvent.change(screen.getByLabelText('Segurar após fala (ms)'), { target: { value: '180' } })
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][2].captions).toMatchObject({ font_family: 'Verdana', weight: 400, italic: true, outline_color: '#112233', box_color: null, gap_tolerance_ms: 420, min_display_ms: 550, hold_ms: 180 })
  })

  it('keeps zoom valid with fit and supports focus plus fill without borders', async () => {
    const save = mockBaseEditor()
    const user = userEvent.setup()
    renderEditor()
    await user.click(await screen.findByRole('button', { name: 'SCREEN' }))
    expect(screen.getByLabelText('Ajuste')).toHaveValue('CONTAIN')
    expect(screen.getByLabelText('Zoom')).toBeDisabled()
    expect(screen.getByLabelText('Foco horizontal')).toBeDisabled()
    expect(screen.getByText(/zoom e foco ficam neutros/i)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Borda'), { target: { value: '12' } })
    await user.click(screen.getByRole('button', { name: 'Preencher sem bordas' }))
    expect(screen.getByLabelText('Zoom')).toBeEnabled()
    fireEvent.change(screen.getByLabelText('Zoom'), { target: { value: '2' } })
    expect(screen.getByLabelText('Ajuste')).toHaveValue('COVER')
    fireEvent.change(screen.getByLabelText('Foco horizontal'), { target: { value: '25' } })
    fireEvent.change(screen.getByLabelText('Foco vertical'), { target: { value: '75' } })
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    const screenLayer = save.mock.calls[0][2].elements.find((item) => item.kind === 'SCREEN')
    expect(screenLayer).toMatchObject({ fit: 'COVER', zoom: 2, focal_x: .25, focal_y: .75, border_width: 0, padding: 0 })
  })

  it('normalizes a saved v1 config before sending it back to the backend', async () => {
    const legacy = createEditConfig()
    for (const element of legacy.elements) { delete (element as Partial<typeof element>).zoom; delete (element as Partial<typeof element>).focal_x; delete (element as Partial<typeof element>).focal_y }
    delete (legacy.captions as Partial<typeof legacy.captions>).outline_color
    delete (legacy.captions as Partial<typeof legacy.captions>).italic
    delete (legacy.captions as Partial<typeof legacy.captions>).gap_tolerance_ms
    delete (legacy.captions as Partial<typeof legacy.captions>).min_display_ms
    delete (legacy.captions as Partial<typeof legacy.captions>).hold_ms
    legacy.captions.font_family = 'Inter'
    vi.spyOn(api, 'getEditConfig').mockResolvedValue({ id: 'edit-old', project_id: 'project-1', candidate_id: 'candidate-1', schema_version: 1, config: legacy, created_at: '', updated_at: '' })
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS', items: [] })
    mockRenderingRecovery()
    const save = vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-old', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' }))
    const user = userEvent.setup()
    renderEditor()
    await screen.findByText('Timing: palavras.')
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][2].elements.every((item) => item.zoom === 1 && item.focal_x === .5 && item.focal_y === .5)).toBe(true)
    expect(save.mock.calls[0][2].captions).toMatchObject({ font_family: 'Inter', outline_color: '#000000', italic: false, gap_tolerance_ms: 250, min_display_ms: 300, hold_ms: 150 })
  })

  it('does not leak candidate A config into candidate B when B has no saved config', async () => {
    const configA = createEditConfig(EDITOR_PRESETS[1])
    configA.banner.text = 'Somente candidato A'
    const candidateB = { ...candidate, id: 'candidate-2', title: 'Clip B' }
    vi.spyOn(api, 'getEditConfig').mockImplementation(async (_projectId, candidateId) => {
      if (candidateId === candidate.id) return { id: 'edit-a', project_id: 'project-1', candidate_id: candidate.id, schema_version: 1, config: configA, created_at: '', updated_at: '' }
      throw new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing')
    })
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS', items: [] })
    mockRenderingRecovery()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} onClose={() => undefined} /></QueryClientProvider>)
    await screen.findByLabelText('Preset de layout')
    view.rerender(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidateB} onClose={() => undefined} /></QueryClientProvider>)
    expect(screen.queryByLabelText('Preset de layout')).not.toBeInTheDocument()
    expect(await screen.findByText('Nenhuma configuração salva. Um preset inicial foi aplicado.')).toBeInTheDocument()
    expect(screen.queryByText('Somente candidato A')).not.toBeInTheDocument()
  })

  it('ignores a late candidate A response after candidate B is ready', async () => {
    let resolveA!: (value: EditConfigResponse) => void
    const lateA = new Promise<EditConfigResponse>((resolve) => { resolveA = resolve })
    const candidateB = { ...candidate, id: 'candidate-2', title: 'Clip B' }
    vi.spyOn(api, 'getEditConfig').mockImplementation(async (_projectId, candidateId) => {
      if (candidateId === candidate.id) return lateA
      throw new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing')
    })
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS', items: [] })
    mockRenderingRecovery()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} onClose={() => undefined} /></QueryClientProvider>)
    expect(screen.queryByLabelText('Preset de layout')).not.toBeInTheDocument()
    view.rerender(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidateB} onClose={() => undefined} /></QueryClientProvider>)
    await screen.findByLabelText('Preset de layout')
    const lateConfig = createEditConfig(EDITOR_PRESETS[2])
    resolveA({ id: 'edit-a', project_id: 'project-1', candidate_id: candidate.id, schema_version: 1, config: lateConfig, created_at: '', updated_at: '' })
    await waitFor(() => expect(screen.getByLabelText('Preset de layout')).toHaveValue('WEBCAM_TOP_SCREEN_BOTTOM'))
  })
})
