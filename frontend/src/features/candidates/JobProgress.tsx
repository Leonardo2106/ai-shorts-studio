import { useEffect, useRef } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { Job } from '../../types/api'

const terminal = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])

export function JobProgress({ jobId, label, onTerminal }: { jobId: string; label: string; onTerminal: (status: Job['status']) => void }) {
  const query = useQuery({ queryKey: ['job', jobId], queryFn: () => api.getJob(jobId), refetchInterval: (value) => { const job = value.state.data as Job | undefined; return job && terminal.has(job.status) ? false : 1000 } })
  const cancel = useMutation({ mutationFn: () => api.cancelJob(jobId), onSuccess: () => query.refetch() })
  const job = query.data
  const notified = useRef<string | null>(null)
  useEffect(() => { if (job && terminal.has(job.status) && notified.current !== `${job.id}:${job.status}`) { notified.current = `${job.id}:${job.status}`; onTerminal(job.status) } }, [job, onTerminal])
  if (query.isError) return <StatusMessage tone="error">Não foi possível acompanhar {label}: {errorMessage(query.error)}</StatusMessage>
  const progress = Math.round(Math.max(0, Math.min(1, job?.progress ?? 0)) * 100)
  return <div className="rounded-lg border border-slate-700 p-3" aria-live="polite"><div className="flex justify-between text-sm"><span>{label}: <strong>{job?.status ?? 'consultando'}</strong></span><span>{progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded bg-slate-800" role="progressbar" aria-label={`Progresso: ${label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><div className="h-full bg-sky-500" style={{ width: `${progress}%` }} /></div>{job && (job.status === 'PENDING' || job.status === 'RUNNING') && <button type="button" className="button-secondary mt-3" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? 'Cancelando…' : 'Cancelar'}</button>}{job?.status === 'FAILED' && <div className="mt-3"><StatusMessage tone="error">{job.error?.message ?? `${label} falhou.`}</StatusMessage></div>}{job?.status === 'CANCELLED' && <div className="mt-3"><StatusMessage>Operação cancelada. Revise as opções e tente novamente.</StatusMessage></div>}</div>
}
