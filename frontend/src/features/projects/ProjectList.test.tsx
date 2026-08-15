import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ProjectList } from './ProjectList'

afterEach(() => vi.unstubAllGlobals())

it('lists projects and opens the selected one', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ id: 'p1', name: 'Demo', media: [], webcam_offset_ms: 0 }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  const select = vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProjectList selectedId={null} onSelect={select} /></QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: /Demo/i }))
  expect(select).toHaveBeenCalledWith('p1')
})

it('creates a project from the real form', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'new-id', name: 'Minha aula', media: [], webcam_offset_ms: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: 'new-id', name: 'Minha aula', media: [], webcam_offset_ms: 0 }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  const select = vi.fn()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProjectList selectedId={null} onSelect={select} /></QueryClientProvider>)
  await userEvent.type(screen.getByLabelText('Novo projeto'), 'Minha aula')
  await userEvent.click(screen.getByRole('button', { name: 'Criar projeto' }))
  expect(select).toHaveBeenCalledWith('new-id')
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects', expect.objectContaining({ method: 'POST', body: JSON.stringify({ name: 'Minha aula' }) }))
})
