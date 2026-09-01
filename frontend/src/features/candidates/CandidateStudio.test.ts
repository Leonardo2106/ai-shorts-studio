import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api/client'
import type { AiProviderCapability, Candidate, Capabilities, Transcript, TranscriptSummary } from '../../types/api'
import { CandidateStudio } from './CandidateStudio'
import { visibleCandidates } from './visibleCandidates'

const candidate = (id: string, status: Candidate['status'] = 'PENDING', transcriptId = 't'): Candidate => ({ id, project_id: 'p', transcript_id: transcriptId, schema_version: 1, start_ms: 0, end_ms: 1000, title: id, status, text: id, origin: 'LOCAL', local_features: {}, reasons: [], context: {}, signals: {}, score: 1, score_breakdown: [], created_at: '', updated_at: '' })

const transcript = (id: string): Transcript => ({ id, project_id: 'p', media_id: 'media', source: 'LOCAL', audio_stream_index: 0, language: 'pt', duration_ms: 60_000, engine: 'whisper', model: 'tiny', segments: [] })
const summaries: TranscriptSummary[] = ['transcript-a', 'transcript-b'].map((id) => ({ id, project_id: 'p', media_id: 'media', language: 'pt', duration_ms: 60_000, created_at: '2026-01-01T00:00:00Z' }))
const allCandidates = [candidate('A-1', 'PENDING', 'transcript-a'), candidate('A-2', 'PENDING', 'transcript-a'), candidate('B-1', 'PENDING', 'transcript-b'), candidate('B-2', 'PENDING', 'transcript-b')]

function renderStudio(capabilities?: Capabilities) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const content = (nextCapabilities?: Capabilities) => createElement(QueryClientProvider, { client: queryClient }, createElement(CandidateStudio, { projectId: 'p', media: [], capabilities: nextCapabilities }))
  const view = render(content(capabilities))
  return { ...view, rerenderCapabilities: (nextCapabilities?: Capabilities) => view.rerender(content(nextCapabilities)) }
}

const provider = (providerId: AiProviderCapability['provider'], models: string[]): AiProviderCapability => ({ provider: providerId, configured: true, models, parameters: ['max_output_tokens', 'temperature', 'timeout_seconds', 'retries', 'chunk_char_limit'] })

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'listTranscripts').mockResolvedValue(summaries)
  vi.spyOn(api, 'getTranscript').mockImplementation(async (_projectId, id) => transcript(id))
  vi.spyOn(api, 'listCandidates').mockResolvedValue(allCandidates)
  vi.spyOn(api, 'listScoreProfiles').mockResolvedValue([])
  vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'SEGMENTS', items: [] })
  vi.spyOn(api, 'estimateAiAnalysis').mockResolvedValue({ chunks: 1, estimated_input_tokens: 100, candidates: 2, planned_provider_calls: 2, max_provider_calls: 8 })
  vi.spyOn(api, 'listProjectJobs').mockResolvedValue([])
})

describe('ranked candidate projection', () => {
  it('shows exactly the returned Top N order and never restores rejected candidates', () => { const items = [candidate('a'), candidate('b'), candidate('c'), candidate('d', 'REJECTED')]; expect(visibleCandidates(items, ['c', 'a'])).toEqual([items[2], items[0]]); expect(visibleCandidates(items, ['d', 'b'])).toEqual([items[1]]) })

  it('drops transcript-scoped state before ranking the newly selected transcript', async () => {
    const rank = vi.spyOn(api, 'rankCandidates').mockImplementation(async (_projectId, transcriptId) => transcriptId === 'transcript-a' ? [allCandidates[1]] : [allCandidates[3]])
    const user = userEvent.setup()
    renderStudio()

    await screen.findAllByText('A-1')
    await user.click(screen.getByRole('button', { name: 'Calcular e ranquear' }))
    await waitFor(() => expect(screen.queryByText('A-1')).not.toBeInTheDocument())
    expect(screen.getAllByText('A-2').length).toBeGreaterThan(0)

    await user.selectOptions(screen.getByLabelText('Transcript de origem'), 'transcript-b')
    await screen.findAllByText('B-1')
    expect(screen.queryByRole('button', { name: /A-2/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /B-2/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Calcular e ranquear' }))
    await waitFor(() => expect(rank).toHaveBeenLastCalledWith('p', 'transcript-b', ['B-1', 'B-2'], null, 10, .5))
  })

  it('refetches caption cues after saving a changed candidate range', async () => {
    vi.spyOn(api, 'rankCandidates').mockResolvedValue([])
    const captions = vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS_AND_SEGMENTS', items: [] })
    vi.spyOn(api, 'updateCandidate').mockImplementation(async (_projectId, id, patch) => ({ ...allCandidates.find((item) => item.id === id)!, ...patch }))
    const user = userEvent.setup()
    renderStudio()

    await waitFor(() => expect(captions).toHaveBeenCalledTimes(1))
    await user.clear(screen.getByLabelText('Fim (ms)'))
    await user.type(screen.getByLabelText('Fim (ms)'), '900')
    await user.click(screen.getByRole('button', { name: 'Salvar corte' }))
    await waitFor(() => expect(captions).toHaveBeenCalledTimes(2))
  })

  it('keeps a running generation job tracked and blocks transcript changes or a second submit', async () => {
    const start = vi.spyOn(api, 'startCandidateGeneration').mockResolvedValue({ id: 'job-running', project_id: 'p', kind: 'CANDIDATE_GENERATION', status: 'PENDING', progress: 0 })
    vi.spyOn(api, 'getJob').mockResolvedValue({ id: 'job-running', project_id: 'p', kind: 'CANDIDATE_GENERATION', status: 'RUNNING', progress: .4 })
    const user = userEvent.setup()
    renderStudio()

    const submit = await screen.findByRole('button', { name: 'Gerar candidatos localmente' })
    await user.click(submit)
    expect(await screen.findByText(/Geração local:/)).toHaveTextContent('executando')
    expect(screen.getByLabelText('Transcript de origem')).toBeDisabled()
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(start).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('progressbar', { name: 'Progresso: Geração local' })).toBeInTheDocument()
  })
})

describe('provider analysis UX', () => {
  it('recovers the newest active semantic job after remount and resumes polling', async () => {
    const recovered = { id: 'ai-recovered-running', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'RUNNING' as const, progress: .45, created_at: '2026-08-20T12:00:00Z' }
    vi.mocked(api.listProjectJobs).mockResolvedValue([recovered, { ...recovered, id: 'ai-older-pending', status: 'PENDING', created_at: '2026-08-20T11:00:00Z' }])
    const getJob = vi.spyOn(api, 'getJob').mockResolvedValue(recovered)
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })

    await waitFor(() => expect(screen.getByText(/Análise textual:/)).toHaveTextContent('executando'))
    expect(getJob).toHaveBeenCalledWith('ai-recovered-running')
    expect(api.listProjectJobs).toHaveBeenCalledWith('p', { kind: 'SEMANTIC_ANALYSIS', limit: 10 })
    expect(screen.getByLabelText('Transcript de origem')).toBeDisabled()
  })

  it('recovers a useful terminal semantic job with its original failure details', async () => {
    const recovered = { id: 'ai-recovered-failed', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'FAILED' as const, progress: .2, error: { code: 'PROVIDER_TIMEOUT', message: 'O provider excedeu o tempo limite.', details: { timeout_seconds: 30 } } }
    vi.mocked(api.listProjectJobs).mockResolvedValue([recovered])
    vi.spyOn(api, 'getJob').mockResolvedValue(recovered)
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })

    expect(await screen.findByText('O provider excedeu o tempo limite.')).toBeInTheDocument()
    expect(screen.getByText('Detalhes técnicos')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revisar opções e tentar novamente' })).toBeInTheDocument()
  })

  it('does not invent an analysis job when project recovery returns no jobs', async () => {
    const user = userEvent.setup()
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })
    await screen.findAllByText('A-1')
    await waitFor(() => expect(api.listProjectJobs).toHaveBeenCalled())
    expect(screen.queryByText(/Análise textual:/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: 'Analisar texto com IA' })).toBeEnabled()
  })

  it('hydrates provider and model when capabilities arrive after mount', async () => {
    const view = renderStudio()
    await screen.findAllByText('A-1')
    expect(screen.getByText(/Nenhum provider externo/)).toBeInTheDocument()

    view.rerenderCapabilities({ ai_providers: [provider('OPENAI', ['gpt-a'])] })

    await waitFor(() => expect(screen.getByLabelText('Provider')).toHaveValue('OPENAI'))
    expect(screen.getByLabelText('Modelo')).toHaveValue('gpt-a')
    expect(await screen.findByText(/Contexto: 1 chunk para 2 candidatos/)).toBeInTheDocument()
    expect(screen.getByText(/Provider externo: 2 chamadas planejadas; limite deste job: 8/)).toBeInTheDocument()
    expect(screen.queryByText(/chunks\/chamadas/)).not.toBeInTheDocument()
  })

  it('preserves a valid user provider and repairs invalid provider or model selections', async () => {
    const user = userEvent.setup()
    const view = renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a']), provider('GEMINI', ['gemini-a', 'gemini-b'])] })
    await screen.findAllByText('A-1')
    await waitFor(() => expect(screen.getByLabelText('Provider')).toHaveValue('OPENAI'))
    await user.selectOptions(screen.getByLabelText('Provider'), 'GEMINI')
    await user.selectOptions(screen.getByLabelText('Modelo'), 'gemini-b')

    view.rerenderCapabilities({ ai_providers: [provider('OPENAI', ['gpt-new']), provider('GEMINI', ['gemini-b', 'gemini-new'])] })
    await waitFor(() => expect(screen.getByLabelText('Provider')).toHaveValue('GEMINI'))
    expect(screen.getByLabelText('Modelo')).toHaveValue('gemini-b')

    view.rerenderCapabilities({ ai_providers: [provider('OPENAI', ['gpt-new'])] })
    await waitFor(() => expect(screen.getByLabelText('Provider')).toHaveValue('OPENAI'))
    expect(screen.getByLabelText('Modelo')).toHaveValue('gpt-new')
  })

  it('posts the selected provider and model with explicit external-processing opt-in', async () => {
    const start = vi.spyOn(api, 'startAiAnalysis').mockResolvedValue({ id: 'ai-job', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'PENDING', progress: 0 })
    vi.spyOn(api, 'getJob').mockResolvedValue({ id: 'ai-job', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'PENDING', progress: 0 })
    const user = userEvent.setup()
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })
    await screen.findAllByText('A-1')
    await waitFor(() => expect(screen.getByLabelText('Modelo')).toHaveValue('gpt-a'))
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Analisar texto com IA' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start).toHaveBeenCalledWith('p', expect.objectContaining({ provider: 'OPENAI', model: 'gpt-a', candidate_ids: ['A-1', 'A-2'], opt_in_external_processing: true }))
    expect(await screen.findByText(/Análise textual:/)).toHaveTextContent('na fila')
  })

  it('keeps a failed provider job visible with its real cause until the user unlocks retry', async () => {
    vi.spyOn(api, 'startAiAnalysis').mockResolvedValue({ id: 'ai-failed', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'PENDING', progress: 0 })
    vi.spyOn(api, 'getJob').mockResolvedValue({ id: 'ai-failed', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'FAILED', progress: .3, error: { code: 'PROVIDER_AUTHENTICATION_FAILED', message: 'O provider recusou a credencial configurada.', details: { provider: 'OPENAI' } } })
    const user = userEvent.setup()
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })
    await screen.findAllByText('A-1')
    await waitFor(() => expect(screen.getByLabelText('Modelo')).toHaveValue('gpt-a'))
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Analisar texto com IA' }))

    expect(await screen.findByText('O provider recusou a credencial configurada.')).toBeInTheDocument()
    expect(screen.getByText('Detalhes técnicos')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analisar texto com IA' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Revisar opções e tentar novamente' }))
    expect(screen.getByRole('button', { name: 'Analisar texto com IA' })).toBeEnabled()
  })

  it('keeps a clear cache notice after a completed provider job is removed', async () => {
    vi.spyOn(api, 'startAiAnalysis').mockResolvedValue({ id: 'ai-cached', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'PENDING', progress: 0 })
    vi.spyOn(api, 'getJob').mockResolvedValue({ id: 'ai-cached', project_id: 'p', kind: 'SEMANTIC_ANALYSIS', status: 'COMPLETED', progress: 1, result: { cache_hit: true, cached_chunks: 1 } })
    const user = userEvent.setup()
    renderStudio({ ai_providers: [provider('OPENAI', ['gpt-a'])] })
    await screen.findAllByText('A-1')
    await waitFor(() => expect(screen.getByLabelText('Modelo')).toHaveValue('gpt-a'))
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Analisar texto com IA' }))

    expect(await screen.findByText(/Análise textual recuperada do cache/)).toHaveTextContent('o provider externo não foi chamado novamente')
    expect(screen.queryByText(/Análise textual:/)).not.toBeInTheDocument()
  })
})
