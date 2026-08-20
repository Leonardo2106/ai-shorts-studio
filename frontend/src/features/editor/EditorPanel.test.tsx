import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api/client'
import type { Candidate, EditConfigResponse } from '../../types/api'
import { EditorPanel } from './EditorPanel'

const candidate: Candidate = { id: 'candidate-1', project_id: 'project-1', transcript_id: 'transcript-1', schema_version: 1, start_ms: 1000, end_ms: 31000, title: 'Clip', status: 'ACCEPTED', text: 'Trecho', origin: 'LOCAL', local_features: {}, reasons: [], context: {}, signals: {}, score: 2, score_breakdown: [], created_at: '', updated_at: '' }

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><EditorPanel projectId="project-1" candidate={candidate} onClose={() => undefined} /></QueryClientProvider>)
}

describe('EditorPanel', () => {
  it('applies presets, edits captions and persists a versioned config', async () => {
    vi.spyOn(api, 'getEditConfig').mockRejectedValue(new ApiError(404, 'EDIT_CONFIG_NOT_FOUND', 'missing'))
    vi.spyOn(api, 'getCandidateCaptions').mockResolvedValue({ timing_source: 'WORDS_AND_SEGMENTS', items: [{ start_ms: 0, end_ms: 1000, text: 'Olá', words: [{ start_ms: 0, end_ms: 1000, text: 'Olá' }] }, { start_ms: 1000, end_ms: 3000, text: 'fallback por segmento', words: null }] })
    const save = vi.spyOn(api, 'saveEditConfig').mockImplementation(async (projectId, candidateId, config) => ({ id: 'edit-1', project_id: projectId, candidate_id: candidateId, schema_version: 1, config, created_at: '', updated_at: '' } satisfies EditConfigResponse))
    const user = userEvent.setup()
    renderEditor()
    expect(await screen.findByText('Timing: palavras quando disponíveis, com fallback por segmento.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Preset de layout'), 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM')
    await user.click(screen.getByRole('button', { name: 'CAPTIONS' }))
    fireEvent.change(screen.getByLabelText('Tamanho da fonte'), { target: { value: '72' } })
    await user.click(screen.getByRole('button', { name: 'Salvar configuração' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][2]).toMatchObject({ schema_version: 1, canvas_width: 1080, canvas_height: 1920, preset: 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM', captions: { font_size: 72 }, banner: { enabled: true } })
  })
})
