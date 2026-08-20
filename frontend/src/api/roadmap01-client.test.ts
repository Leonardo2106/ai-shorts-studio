import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('Roadmap 01 API client', () => {
  it('uses explicit opt-in and the semantic analysis endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'job-1' }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.startAiAnalysis('project-1', { provider: 'OPENAI', model: 'gpt-test', candidate_ids: ['candidate-1'], opt_in_external_processing: true, max_output_tokens: 1024, timeout_seconds: 30, retries: 1, chunk_char_limit: 6000 })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project-1/semantic-analysis-jobs', expect.objectContaining({ method: 'POST', body: expect.stringContaining('"opt_in_external_processing":true') }))
  })

  it('persists accept/reject decisions through candidate patch', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'candidate-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.updateCandidate('project-1', 'candidate-1', { status: 'ACCEPTED' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project-1/candidates/candidate-1', expect.objectContaining({ method: 'PATCH', body: '{"status":"ACCEPTED"}' }))
  })

  it('scopes ranking to transcript and eligible candidate ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ id: 'candidate-2' }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await api.rankCandidates('project-1', 'transcript-1', ['candidate-1', 'candidate-2'], null, 1, .5)
    expect(result).toEqual([{ id: 'candidate-2' }])
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project-1/candidates/rank', expect.objectContaining({ body: JSON.stringify({ transcript_id: 'transcript-1', candidate_ids: ['candidate-1', 'candidate-2'], profile_id: null, top_n: 1, max_overlap_ratio: .5 }) }))
  })
})
