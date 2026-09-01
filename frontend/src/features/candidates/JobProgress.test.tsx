import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../../api/client'
import type { Job, JobStatus } from '../../types/api'
import { JobProgress } from './JobProgress'

function terminal(status: JobStatus): Job { return { id: `job-${status}`, project_id: 'project', kind: 'TEST', status, progress: 1, error: status === 'FAILED' ? { code: 'PROVIDER_ERROR', message: 'Falhou ao chamar o provider', details: { cause: 'modelo indisponível' } } : null } }
function renderJob(job: Job, onTerminal = vi.fn(), onRetry?: () => void) { vi.spyOn(api, 'getJob').mockResolvedValue(job); const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); render(<QueryClientProvider client={client}><JobProgress jobId={job.id} label="Teste" onTerminal={onTerminal} onRetry={onRetry} cacheDescription="O provider externo não foi chamado novamente." /></QueryClientProvider>); return onTerminal }

describe('JobProgress', () => {
  it.each(['FAILED', 'CANCELLED'] as const)('notifies %s without hiding the terminal job and allows retry', async (status) => {
    const retry = vi.fn()
    const job = terminal(status)
    const onTerminal = renderJob(job, vi.fn(), retry)
    await waitFor(() => expect(onTerminal).toHaveBeenCalledWith(status, job))
    expect(screen.getByText(`Teste:`, { exact: false })).toHaveTextContent(status === 'FAILED' ? 'falhou' : 'cancelado')
    await userEvent.click(screen.getByRole('button', { name: 'Revisar opções e tentar novamente' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('shows pending as queued rather than already executing', async () => {
    renderJob({ ...terminal('PENDING'), progress: 0 })
    await waitFor(() => expect(screen.getByText(/Teste:/)).toHaveTextContent('na fila'))
    expect(screen.getByText(/Aguardando uma vaga/)).toBeInTheDocument()
  })

  it('preserves structured failure details behind an expandable section', async () => {
    renderJob(terminal('FAILED'))
    expect(await screen.findByText('Falhou ao chamar o provider')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Detalhes técnicos'))
    expect(screen.getByText(/PROVIDER_ERROR/)).toBeInTheDocument()
    expect(screen.getByText(/modelo indisponível/)).toBeInTheDocument()
  })

  it.each([{ cached: true }, { cache_hit: true }])('identifies a cached result and explains that the provider was not called', async (result) => {
    renderJob({ ...terminal('COMPLETED'), result })
    expect(await screen.findByText(/Resultado recuperado do cache/)).toHaveTextContent('O provider externo não foi chamado novamente.')
  })

  it.each([
    [409, 'JOB_NOT_CANCELLABLE', 'O job já terminou e não pode ser cancelado.'],
    [500, 'CANCEL_FAILED', 'O processo não respondeu ao cancelamento.'],
  ] as const)('shows a cancel error returned with HTTP %i', async (status, code, message) => {
    vi.spyOn(api, 'cancelJob').mockRejectedValue(new ApiError(status, code, message))
    renderJob({ ...terminal('RUNNING'), progress: .4 })
    await userEvent.click(await screen.findByRole('button', { name: 'Cancelar' }))
    expect(await screen.findByText(`Não foi possível cancelar Teste: ${message}`)).toBeInTheDocument()
    expect(screen.getByText('executando')).toBeInTheDocument()
  })
})
