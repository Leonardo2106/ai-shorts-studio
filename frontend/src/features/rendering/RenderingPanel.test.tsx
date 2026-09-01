import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api/client'
import type { EditConfigResponse, Job } from '../../types/api'
import { RenderingPanel } from './RenderingPanel'

const saved = { id: 'edit-1', project_id: 'project-1', candidate_id: 'candidate-1', schema_version: 1, config: {} as EditConfigResponse['config'], created_at: '', updated_at: '' }
const completed: Job = { id: 'job-1', project_id: 'project-1', kind: 'RENDER_PREVIEW', status: 'COMPLETED', progress: 1, result: { artifact_id: 'artifact-1' } }
const plan = { schema_version: 1 as const, project_id: 'project-1', candidate_id: 'candidate-1', edit_config_id: 'edit-1', kind: 'PREVIEW' as const, quality: 'FAST' as const, clip: { timeline_start_ms: 0, timeline_end_ms: 1000, duration_ms: 1000 }, canvas: { logical_width: 1080, logical_height: 1920, output_width: 360, output_height: 640, fps: 24 }, layers: [], captions: { enabled: false }, banner: { enabled: false }, audio: { mode: 'SILENT' }, edit_config_fingerprint: 'b'.repeat(64), dependency_fingerprint: 'a'.repeat(64), cacheable: true }

afterEach(() => vi.restoreAllMocks())
beforeEach(() => { vi.spyOn(api, 'listRenderJobs').mockResolvedValue([]); vi.spyOn(api, 'listRenderArtifacts').mockResolvedValue([]) })

function renderPanel(props?: Partial<React.ComponentProps<typeof RenderingPanel>>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const defaults = { projectId: 'project-1', candidateId: 'candidate-1', configSignature: 'config-a', configDirty: true, saveConfig: vi.fn().mockResolvedValue(saved) }
  const renderResult = render(<QueryClientProvider client={client}><RenderingPanel {...defaults} {...props} /></QueryClientProvider>)
  return { ...renderResult, defaults }
}

describe('RenderingPanel', () => {
  it('saves EditConfig before requesting a real preview and exposes the artifact', async () => {
    const order: string[] = []
    const saveConfig = vi.fn().mockImplementation(async () => { order.push('save'); return saved })
    vi.spyOn(api, 'startPreviewRender').mockImplementation(async () => { order.push('preview'); return completed })
    vi.spyOn(api, 'getJob').mockResolvedValue(completed)
    vi.spyOn(api, 'getRenderArtifact').mockResolvedValue(artifact('/api/v1/projects/project-1/artifacts/artifact-1/content'))
    renderPanel({ saveConfig })

    await userEvent.click(screen.getByRole('button', { name: 'Gerar preview real' }))
    await waitFor(() => expect(api.getRenderArtifact).toHaveBeenCalledWith('project-1', 'artifact-1'))
    expect(order).toEqual(['save', 'preview'])
    expect(screen.getByRole('link', { name: 'Baixar preview ffmpeg' })).toHaveAttribute('href', '/api/v1/projects/project-1/artifacts/artifact-1/content')
  })

  it('marks an existing FFmpeg preview stale after the editor config changes', async () => {
    vi.spyOn(api, 'startPreviewRender').mockResolvedValue(completed)
    vi.spyOn(api, 'getRenderPlan').mockResolvedValue(plan)
    vi.spyOn(api, 'getJob').mockResolvedValue(completed)
    vi.spyOn(api, 'getRenderArtifact').mockResolvedValue(artifact('/preview.mp4'))
    const view = renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Gerar preview real' }))
    await screen.findByRole('link', { name: 'Baixar preview ffmpeg' })
    view.rerender(<QueryClientProvider client={new QueryClient()}><RenderingPanel {...view.defaults} configSignature="config-b" /></QueryClientProvider>)
    expect(await screen.findByText(/layout mudou desde o último preview/i)).toBeInTheDocument()
  })

  it('requests real cancellation for a running final render', async () => {
    const running: Job = { ...completed, id: 'job-running', kind: 'RENDER_FINAL', status: 'RUNNING', progress: 0.27, result: null }
    vi.spyOn(api, 'startFinalRender').mockResolvedValue(running)
    vi.spyOn(api, 'getJob').mockResolvedValue(running)
    const cancel = vi.spyOn(api, 'cancelJob').mockResolvedValue({ ...running, cancellation_requested: true })
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Renderizar Short' }))
    expect(await screen.findByText('27%')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('job-running'))
  })

  it('keeps the running job visible and explains a cancellation failure', async () => {
    const running: Job = { ...completed, id: 'job-running', kind: 'RENDER_FINAL', status: 'RUNNING', progress: 0.27, result: null }
    vi.spyOn(api, 'startFinalRender').mockResolvedValue(running)
    vi.spyOn(api, 'getJob').mockResolvedValue(running)
    vi.spyOn(api, 'cancelJob').mockRejectedValue(new ApiError(409, 'JOB_CANCEL_FAILED', 'O processo ainda não aceitou o cancelamento.', { technical: 'termination request rejected' }))
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Renderizar Short' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancelar' }))
    expect(await screen.findByText('Não foi possível cancelar o render. O job continua sendo acompanhado.')).toBeInTheDocument()
    expect(screen.getByText('Possível causa: O processo ainda não aceitou o cancelamento.')).toBeInTheDocument()
    expect(screen.getByText('27%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeEnabled()
    await userEvent.click(screen.getByText('Detalhes técnicos'))
    expect(screen.getByText('termination request rejected')).toBeInTheDocument()
  })

  it('recovers a running job after unmount and keeps polling/cancellation available', async () => {
    const running: Job = { ...completed, id: 'job-recovered', kind: 'RENDER_PREVIEW', status: 'RUNNING', progress: 0.42, result: null, created_at: '2026-08-20T10:00:00Z' }
    vi.mocked(api.listRenderJobs).mockImplementation(async (_projectId, _candidateId, kind) => kind === 'RENDER_PREVIEW' ? [running] : [])
    vi.spyOn(api, 'getJob').mockResolvedValue(running)
    const cancel = vi.spyOn(api, 'cancelJob').mockResolvedValue({ ...running, cancellation_requested: true })
    const first = renderPanel()
    expect(await screen.findByText('42%')).toBeInTheDocument()
    first.unmount()

    renderPanel()
    expect(await screen.findByText('42%')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('job-recovered'))
    expect(api.listRenderJobs).toHaveBeenCalledTimes(4)
  })

  it('recovers only artifacts belonging to the selected candidate', async () => {
    const wrong = { ...artifact('/wrong.mp4'), id: 'wrong', candidate_id: 'candidate-other' }
    const correct = artifact('/correct.mp4')
    vi.mocked(api.listRenderArtifacts).mockImplementation(async (_projectId, _candidateId, kind) => kind === 'PREVIEW' ? [wrong, correct] : [])
    renderPanel({ configDirty: false })
    expect(await screen.findByRole('link', { name: 'Baixar preview ffmpeg' })).toHaveAttribute('href', '/correct.mp4')
    expect(screen.queryByText('/wrong.mp4')).not.toBeInTheDocument()
  })

  it('uses the backend dependency fingerprint to mark a recovered preview stale', async () => {
    vi.mocked(api.listRenderArtifacts).mockImplementation(async (_projectId, _candidateId, kind) => kind === 'PREVIEW' ? [artifact('/preview.mp4')] : [])
    vi.spyOn(api, 'getRenderPlan').mockResolvedValue({ ...plan, dependency_fingerprint: 'different'.padEnd(64, '0') })
    renderPanel({ configDirty: false })
    expect(await screen.findByText(/layout mudou desde o último preview/i)).toBeInTheDocument()
    expect(api.getRenderPlan).toHaveBeenCalledWith('project-1', 'candidate-1', 'PREVIEW', 'FAST')
  })

  it('shows sanitized human context and technical job details on demand', async () => {
    const failed: Job = { ...completed, id: 'failed', status: 'FAILED', progress: 0.3, result: null, error: { code: 'FFMPEG_FAILED', message: 'A webcam não pôde ser decodificada.', details: { technical: 'decoder returned status 1' } } }
    vi.mocked(api.listRenderJobs).mockImplementation(async (_projectId, _candidateId, kind) => kind === 'RENDER_PREVIEW' ? [failed] : [])
    vi.spyOn(api, 'getJob').mockResolvedValue(failed)
    renderPanel()
    expect(await screen.findByText('Possível causa: A webcam não pôde ser decodificada.')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Detalhes técnicos'))
    expect(screen.getByText('decoder returned status 1')).toBeInTheDocument()
  })
})

function artifact(contentUrl: string) { return { id: 'artifact-1', project_id: 'project-1', candidate_id: 'candidate-1', edit_config_id: 'edit-1', job_id: 'job-1', kind: 'PREVIEW' as const, quality: 'FAST' as const, dependency_fingerprint: 'a'.repeat(64), content_url: contentUrl, size_bytes: 1024, duration_ms: 1000, width: 360, height: 640, has_audio: true, created_at: '' } }
