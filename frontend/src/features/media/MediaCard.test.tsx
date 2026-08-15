import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { MediaAsset } from '../../types/api'
import { MediaCard } from './MediaCard'

afterEach(() => vi.unstubAllGlobals())

const associated: MediaAsset = {
  id: 'm1', role: 'SCREEN', original_filename: 'screen.mp4', size_bytes: 1000, sha256: 'abc', content_url: '/api/v1/content',
  probe: { duration_ms: 1000, format_name: 'mp4', bitrate: null, video_streams: [], audio_streams: [] },
}

it('does not offer replacement when the role is already associated', () => {
  render(<QueryClientProvider client={new QueryClient()}><MediaCard projectId="p1" role="SCREEN" media={associated} /></QueryClientProvider>)
  expect(screen.getByText('Associada')).toBeInTheDocument()
  expect(screen.queryByLabelText('Importar Tela')).not.toBeInTheDocument()
  expect(screen.queryByText('Substituir')).not.toBeInTheDocument()
})

it('invalidates project detail and list caches after upload', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(associated), { status: 201, headers: { 'Content-Type': 'application/json' } })))
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(<QueryClientProvider client={client}><MediaCard projectId="p1" role="SCREEN" /></QueryClientProvider>)
  await userEvent.upload(screen.getByLabelText('Importar Tela'), new File(['video'], 'screen.mp4', { type: 'video/mp4' }))
  await waitFor(() => {
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['project', 'p1'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['projects'] })
  })
})
