import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '../../api/client'
import type { Job, JobStatus } from '../../types/api'
import { JobProgress } from './JobProgress'

function terminal(status: JobStatus): Job { return { id: `job-${status}`, project_id: 'project', kind: 'TEST', status, progress: 1, error: status === 'FAILED' ? { message: 'Falhou' } : null } }
function renderJob(status: JobStatus, onTerminal: (status: JobStatus) => void) { vi.spyOn(api, 'getJob').mockResolvedValue(terminal(status)); const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); render(<QueryClientProvider client={client}><JobProgress jobId={`job-${status}`} label="Teste" onTerminal={onTerminal} /></QueryClientProvider>) }

describe('JobProgress', () => {
  it.each(['FAILED', 'CANCELLED'] as const)('notifies %s so callers can unlock retry', async (status) => { const onTerminal = vi.fn(); renderJob(status, onTerminal); await waitFor(() => expect(onTerminal).toHaveBeenCalledWith(status)); expect(onTerminal).toHaveBeenCalledTimes(1) })
})
