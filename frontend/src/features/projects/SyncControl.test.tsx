import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { SyncControl } from './SyncControl'

afterEach(() => vi.unstubAllGlobals())

it('persists a negative integer offset', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'p1', webcam_offset_ms: -375 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  render(<QueryClientProvider client={new QueryClient()}><SyncControl projectId="p1" initialOffset={0} /></QueryClientProvider>)
  const field = screen.getByLabelText('Offset da webcam (ms)')
  await userEvent.clear(field)
  await userEvent.type(field, '-375')
  await userEvent.click(screen.getByRole('button', { name: 'Salvar offset' }))
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/p1/sync', expect.objectContaining({ body: JSON.stringify({ webcam_offset_ms: -375 }) }))
  expect(await screen.findByText('Offset persistido.')).toBeInTheDocument()
})
