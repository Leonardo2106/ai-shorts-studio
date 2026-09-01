import { useEffect, useRef } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { Job } from '../../types/api'

const terminal = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])

const statusLabel: Record<Job['status'], string> = {
  PENDING: 'na fila',
  RUNNING: 'executando',
  COMPLETED: 'concluído',
  FAILED: 'falhou',
  CANCELLED: 'cancelado',
}

export function JobProgress({ jobId, label, onTerminal, onRetry, cacheDescription = 'A etapa correspondente não foi executada novamente.' }: { jobId: string; label: string; onTerminal: (status: Job['status'], job: Job) => void; onRetry?: () => void; cacheDescription?: string }) {
  const query = useQuery({ queryKey: ['job', jobId], queryFn: () => api.getJob(jobId), refetchInterval: (value) => { const job = value.state.data as Job | undefined; return job && terminal.has(job.status) ? false : 1000 } })
  const cancel = useMutation({ mutationFn: () => api.cancelJob(jobId), onSuccess: () => query.refetch() })
  const job = query.data
  const notified = useRef<string | null>(null)
  useEffect(() => { if (job && terminal.has(job.status) && notified.current !== `${job.id}:${job.status}`) { notified.current = `${job.id}:${job.status}`; onTerminal(job.status, job) } }, [job, onTerminal])
  if (query.isError) return <StatusMessage tone="error">Não foi possível acompanhar {label}: {errorMessage(query.error)}</StatusMessage>
  const progress = Math.round(Math.max(0, Math.min(1, job?.progress ?? 0)) * 100)
  const cacheHit = Boolean(job?.result?.cache_hit ?? job?.result?.cached)
  return <div className="rounded-lg border border-slate-700 p-3" aria-live="polite"><div className="flex justify-between text-sm"><span>{label}: <strong>{job ? statusLabel[job.status] : 'consultando'}</strong></span><span>{progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded bg-slate-800" role="progressbar" aria-label={`Progresso: ${label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><div className="h-full bg-sky-500" style={{ width: `${progress}%` }} /></div>{job?.status === 'PENDING' && <p className="mt-2 text-sm text-slate-400">Aguardando uma vaga no processador local.</p>}{job && (job.status === 'PENDING' || job.status === 'RUNNING') && <button type="button" className="button-secondary mt-3" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? 'Cancelando…' : 'Cancelar'}</button>}{cancel.isError && <div className="mt-3"><StatusMessage tone="error">Não foi possível cancelar {label}: {errorMessage(cancel.error)}</StatusMessage></div>}{job?.status === 'COMPLETED' && cacheHit && <StatusMessage>Resultado recuperado do cache. {cacheDescription}</StatusMessage>}{job?.status === 'FAILED' && <div className="mt-3 space-y-2"><StatusMessage tone="error">{job.error?.message ?? `${label} falhou.`}</StatusMessage>{job.error && (job.error.code || job.error.details !== undefined) && <details className="rounded border border-rose-900/70 bg-rose-950/20 p-2 text-sm"><summary className="cursor-pointer font-medium">Detalhes técnicos</summary>{job.error.code && <p className="mt-2"><strong>Código:</strong> {job.error.code}</p>}{job.error.details !== undefined && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-300">{formatDetails(job.error.details)}</pre>}</details>}</div>}{job?.status === 'CANCELLED' && <div className="mt-3"><StatusMessage>Operação cancelada. Os dados originais foram preservados.</StatusMessage></div>}{job && (job.status === 'FAILED' || job.status === 'CANCELLED') && onRetry && <button type="button" className="button-secondary mt-3" onClick={onRetry}>Revisar opções e tentar novamente</button>}</div>
}

function formatDetails(details: unknown): string {
  if (typeof details === 'string') return details
  try { return JSON.stringify(details, null, 2) }
  catch { return 'Detalhes técnicos indisponíveis.' }
}
