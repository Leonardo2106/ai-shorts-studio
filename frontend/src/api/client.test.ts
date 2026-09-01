import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('API client', () => {
  it('sends sync offset using the documented contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'project-1', webcam_offset_ms: -250 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.updateSync('project-1', -250)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project-1/sync', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ webcam_offset_ms: -250 }) }))
  })

  it('uploads media as multipart without forcing content-type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'media-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.uploadMedia('project-1', 'SCREEN', new File(['video'], 'screen.mp4', { type: 'video/mp4' }))
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(options.body).toBeInstanceOf(FormData)
    expect(options.headers).not.toHaveProperty('Content-Type')
    expect((options.body as FormData).get('role')).toBe('SCREEN')
  })

  it('maps the uniform backend error to ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'INVALID_MEDIA', message: 'Arquivo inválido', details: { reason: 'probe' } } }), { status: 422, headers: { 'Content-Type': 'application/json' } })))
    await expect(api.getProject('missing')).rejects.toMatchObject({ status: 422, code: 'INVALID_MEDIA', message: 'Arquivo inválido' } satisfies Partial<ApiError>)
  })

  it('uses candidate-scoped rendering endpoints and a constrained quality payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'job-1' }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.startFinalRender('project / 1', 'candidate / 1', 'BALANCED')
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project%20%2F%201/candidates/candidate%20%2F%201/render-jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify({ quality: 'BALANCED' }) }))
  })

  it('scopes render recovery to the encoded project, candidate and kind', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.listRenderJobs('project / 1', 'candidate / 1', 'RENDER_PREVIEW')
    await api.listRenderArtifacts('project / 1', 'candidate / 1', 'PREVIEW')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/project%20%2F%201/render-jobs?candidate_id=candidate%20%2F%201&kind=RENDER_PREVIEW')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/projects/project%20%2F%201/artifacts?candidate_id=candidate+%2F+1&kind=PREVIEW')
  })

  it('lists only project-scoped semantic jobs with encoded filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.listProjectJobs('project / 1', { kind: 'SEMANTIC_ANALYSIS', status: 'RUNNING', limit: 10 })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/project%20%2F%201/jobs?kind=SEMANTIC_ANALYSIS&status=RUNNING&limit=10', expect.any(Object))
  })
})
