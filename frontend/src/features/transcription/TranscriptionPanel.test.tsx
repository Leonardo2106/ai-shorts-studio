import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { MediaAsset } from '../../types/api'
import { TranscriptionPanel } from './TranscriptionPanel'

afterEach(() => vi.unstubAllGlobals())

const media: MediaAsset = {
  id: 'm1', role: 'SCREEN', original_filename: 'screen.mp4', size_bytes: 100, sha256: 'abc', content_url: '/content',
  probe: { duration_ms: 1000, format_name: 'mp4', bitrate: null, video_streams: [], audio_streams: [{ index: 2, codec_name: 'aac', sample_rate: 48000, channels: 2, channel_layout: 'stereo', language: 'pt', metadata: {} }] },
}

it('starts a job with explicit source and track then loads its transcript', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init
    const url = String(input)
    if (url.endsWith('/transcription-jobs')) return new Response(JSON.stringify({ id: 'j1', project_id: 'p1', kind: 'TRANSCRIPTION', status: 'COMPLETED', progress: 1, result: { transcript_id: 't1' } }), { status: 202, headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/jobs/j1')) return new Response(JSON.stringify({ id: 'j1', project_id: 'p1', kind: 'TRANSCRIPTION', status: 'COMPLETED', progress: 1, result: { transcript_id: 't1' } }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/projects/p1/transcripts/t1')) return new Response(JSON.stringify({ id: 't1', project_id: 'p1', media_id: 'm1', source: 'SCREEN', audio_stream_index: 2, language: 'pt', duration_ms: 1000, engine: 'faster-whisper', model: 'small', segments: [{ start_ms: 0, end_ms: 1000, text: 'Transcrição real.' }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    return new Response(null, { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TranscriptionPanel projectId="p1" media={[media]} /></QueryClientProvider>)
  await userEvent.selectOptions(screen.getByLabelText('Fonte de mídia'), 'm1')
  await userEvent.selectOptions(screen.getByLabelText('Track de áudio'), '2')
  await userEvent.click(screen.getByRole('button', { name: 'Iniciar transcrição' }))
  expect(await screen.findByText('Transcrição real.')).toBeInTheDocument()
  expect(screen.getByText('100%')).toBeInTheDocument()
  const startCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/transcription-jobs'))
  expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({ media_id: 'm1', audio_stream_index: 2, preset: 'BALANCED', word_timestamps: true })
})

it('explains and blocks transcription when faster-whisper is unavailable', async () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  render(<QueryClientProvider client={new QueryClient()}><TranscriptionPanel projectId="p1" media={[media]} transcriptionAvailable={false} /></QueryClientProvider>)
  await userEvent.selectOptions(screen.getByLabelText('Fonte de mídia'), 'm1')
  await userEvent.selectOptions(screen.getByLabelText('Track de áudio'), '2')
  expect(screen.getByText(/faster-whisper não está disponível/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Iniciar transcrição' })).toBeDisabled()
  expect(fetchMock).not.toHaveBeenCalled()
})
