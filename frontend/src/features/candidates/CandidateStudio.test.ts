import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api/client'
import type { Candidate, Transcript, TranscriptSummary } from '../../types/api'
import { CandidateStudio } from './CandidateStudio'
import { visibleCandidates } from './visibleCandidates'

const candidate = (id: string, status: Candidate['status'] = 'PENDING', transcriptId = 't'): Candidate => ({ id, project_id: 'p', transcript_id: transcriptId, schema_version: 1, start_ms: 0, end_ms: 1000, title: id, status, text: id, origin: 'LOCAL', local_features: {}, reasons: [], context: {}, signals: {}, score: 1, score_breakdown: [], created_at: '', updated_at: '' })

const transcript = (id: string): Transcript => ({ id, project_id: 'p', media_id: 'media', source: 'LOCAL', audio_stream_index: 0, language: 'pt', duration_ms: 60_000, engine: 'whisper', model: 'tiny', segments: [] })
const summaries: TranscriptSummary[] = ['transcript-a', 'transcript-b'].map((id) => ({ id, project_id: 'p', media_id: 'media', language: 'pt', duration_ms: 60_000, created_at: '2026-01-01T00:00:00Z' }))
const allCandidates = [candidate('A-1', 'PENDING', 'transcript-a'), candidate('A-2', 'PENDING', 'transcript-a'), candidate('B-1', 'PENDING', 'transcript-b'), candidate('B-2', 'PENDING', 'transcript-b')]

function renderStudio() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(createElement(QueryClientProvider, { client: queryClient }, createElement(CandidateStudio, { projectId: 'p', media: [] })))
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'listTranscripts').mockResolvedValue(summaries)
  vi.spyOn(api, 'getTranscript').mockImplementation(async (_projectId, id) => transcript(id))
  vi.spyOn(api, 'listCandidates').mockResolvedValue(allCandidates)
  vi.spyOn(api, 'listScoreProfiles').mockResolvedValue([])
  vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'SEGMENTS', items: [] })
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
    expect(await screen.findByText(/Geração local:/)).toHaveTextContent('RUNNING')
    expect(screen.getByLabelText('Transcript de origem')).toBeDisabled()
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(start).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('progressbar', { name: 'Progresso: Geração local' })).toBeInTheDocument()
  })
})
