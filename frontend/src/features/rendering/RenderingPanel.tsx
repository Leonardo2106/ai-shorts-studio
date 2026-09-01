import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, artifactContentUrl, errorMessage, mediaContentUrl } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { EditConfigResponse, Job, RenderQuality } from '../../types/api'

const terminal = new Set<Job['status']>(['COMPLETED', 'FAILED', 'CANCELLED'])
const qualityLabels: Record<RenderQuality, string> = { FAST: 'Rápida', BALANCED: 'Balanceada', HIGH: 'Alta' }
const statusLabels: Record<Job['status'], string> = { PENDING: 'Preparando render…', RUNNING: 'Processando…', COMPLETED: 'Concluído', FAILED: 'Falhou', CANCELLED: 'Cancelado' }

interface RenderRun { jobId: string; configSignature: string | null }

export function RenderingPanel({ projectId, candidateId, configSignature, configDirty, saveConfig }: {
  projectId: string
  candidateId: string
  configSignature: string
  configDirty: boolean
  saveConfig: () => Promise<EditConfigResponse>
}) {
  const [quality, setQuality] = useState<RenderQuality>('BALANCED')
  const [previewRun, setPreviewRun] = useState<RenderRun | null>(null)
  const [finalRun, setFinalRun] = useState<RenderRun | null>(null)
  const previewJobs = useQuery({ queryKey: ['render-jobs', projectId, candidateId, 'RENDER_PREVIEW'], queryFn: () => api.listRenderJobs(projectId, candidateId, 'RENDER_PREVIEW') })
  const finalJobs = useQuery({ queryKey: ['render-jobs', projectId, candidateId, 'RENDER_FINAL'], queryFn: () => api.listRenderJobs(projectId, candidateId, 'RENDER_FINAL') })
  const previewArtifacts = useQuery({ queryKey: ['render-artifacts', projectId, candidateId, 'PREVIEW'], queryFn: () => api.listRenderArtifacts(projectId, candidateId, 'PREVIEW') })
  const finalArtifacts = useQuery({ queryKey: ['render-artifacts', projectId, candidateId, 'FINAL'], queryFn: () => api.listRenderArtifacts(projectId, candidateId, 'FINAL') })
  const recoveredPreview = previewArtifacts.data?.find((item) => item.candidate_id === candidateId && item.kind === 'PREVIEW')
  const recoveredFinal = finalArtifacts.data?.find((item) => item.candidate_id === candidateId && item.kind === 'FINAL')
  useEffect(() => { if (!previewRun) { const job = mostRelevantJob(previewJobs.data); if (job) setPreviewRun({ jobId: job.id, configSignature: null }) } }, [previewJobs.data, previewRun])
  useEffect(() => { if (!finalRun) { const job = mostRelevantJob(finalJobs.data); if (job) setFinalRun({ jobId: job.id, configSignature: null }) } }, [finalJobs.data, finalRun])
  const preview = useRenderJob(projectId, previewRun, recoveredPreview)
  const final = useRenderJob(projectId, finalRun, recoveredFinal)
  const currentPreviewPlan = useQuery({ queryKey: ['render-plan-fingerprint', projectId, candidateId, configSignature], queryFn: () => api.getRenderPlan(projectId, candidateId, 'PREVIEW', 'FAST'), enabled: !configDirty && Boolean(preview.artifact), retry: false })
  const startPreview = useMutation({ mutationFn: async () => { await saveConfig(); return api.startPreviewRender(projectId, candidateId, 'FAST') }, onSuccess: (job) => setPreviewRun({ jobId: job.id, configSignature }) })
  const startFinal = useMutation({ mutationFn: async () => { await saveConfig(); return api.startFinalRender(projectId, candidateId, quality) }, onSuccess: (job) => setFinalRun({ jobId: job.id, configSignature }) })
  const priorPreview = Boolean(preview.job || preview.artifact)
  const localSignatureStale = previewRun?.configSignature != null && previewRun.configSignature !== configSignature
  const fingerprintStale = Boolean(preview.artifact && currentPreviewPlan.data && preview.artifact.dependency_fingerprint !== currentPreviewPlan.data.dependency_fingerprint)
  const previewStale = priorPreview && (configDirty || localSignatureStale || fingerprintStale)
  const busy = startPreview.isPending || startFinal.isPending || preview.active || final.active
  const recoveryError = previewJobs.error ?? finalJobs.error ?? previewArtifacts.error ?? finalArtifacts.error ?? currentPreviewPlan.error

  return <section className="rounded-xl border border-slate-700 bg-slate-950/50 p-4 space-y-4" aria-labelledby="rendering-title">
    <div><p className="text-xs font-semibold uppercase tracking-wider text-violet-400">Rendering</p><h4 id="rendering-title" className="font-semibold">Preview real e arquivo final</h4><p className="text-xs text-slate-400">O Editor Preview acima é instantâneo. FFmpeg só roda quando você solicitar.</p></div>
    <div className="grid gap-3 sm:grid-cols-3" aria-label="Etapas de visualização">
      <Stage title="Editor Preview" state={configDirty ? 'Alterações locais' : 'Configuração salva'} />
      <Stage title="FFmpeg Preview" state={previewStale ? 'Desatualizado' : preview.job ? statusLabels[preview.job.status] : preview.artifact ? 'Concluído' : 'Não gerado'} warning={previewStale} />
      <Stage title="Final Render" state={final.job ? statusLabels[final.job.status] : final.artifact ? 'Concluído' : 'Não gerado'} />
    </div>
    {previewStale && <StatusMessage>O layout mudou desde o último preview FFmpeg. Gere outro preview para validar essas alterações.</StatusMessage>}
    {recoveryError && <StatusMessage tone="error">Não foi possível recuperar renders anteriores: {errorMessage(recoveryError)}</StatusMessage>}
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-3"><h5 className="font-medium">FFmpeg Preview</h5><p className="text-sm text-slate-400">Versão leve e curta, com layout, sincronização, legendas e banner.</p><button type="button" className="button-secondary" disabled={busy} onClick={() => startPreview.mutate()}>{startPreview.isPending ? 'Salvando e preparando…' : preview.job?.status === 'COMPLETED' || preview.artifact ? 'Gerar novo preview' : 'Gerar preview real'}</button>{startPreview.isError && <RenderError title="Não foi possível iniciar o preview." error={startPreview.error} />}{(previewRun || preview.artifact) && <RenderJobView label="Preview FFmpeg" state={preview} stale={previewStale} />}</div>
      <div className="space-y-3"><h5 className="font-medium">Final Render</h5><fieldset><legend className="label">Qualidade</legend><div className="flex flex-wrap gap-3">{(['FAST', 'BALANCED', 'HIGH'] as const).map((value) => <label key={value} className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm"><input type="radio" name="render-quality" value={value} checked={quality === value} disabled={busy} onChange={() => setQuality(value)} />{qualityLabels[value]}</label>)}</div></fieldset><button type="button" className="button" disabled={busy} onClick={() => startFinal.mutate()}>{startFinal.isPending ? 'Salvando e preparando…' : final.job?.status === 'COMPLETED' || final.artifact ? 'Renderizar novamente' : 'Renderizar Short'}</button>{startFinal.isError && <RenderError title="Não foi possível iniciar o render." error={startFinal.error} />}{(finalRun || final.artifact) && <RenderJobView label="Render final" state={final} />}</div>
    </div>
  </section>
}

function Stage({ title, state, warning = false }: { title: string; state: string; warning?: boolean }) { return <div className={`rounded-lg border p-3 ${warning ? 'border-amber-600 bg-amber-950/20' : 'border-slate-700'}`}><strong className="block text-sm">{title}</strong><span className="text-xs text-slate-400">{state}</span></div> }

function mostRelevantJob(jobs: Job[] | undefined): Job | undefined { return jobs?.find((job) => job.status === 'PENDING' || job.status === 'RUNNING') ?? jobs?.[0] }

function useRenderJob(projectId: string, run: RenderRun | null, recoveredArtifact?: import('../../types/api').RenderArtifact) {
  const query = useQuery({ queryKey: ['render-job', run?.jobId], queryFn: () => api.getJob(run!.jobId), enabled: Boolean(run), refetchInterval: (value) => { const job = value.state.data as Job | undefined; return job && terminal.has(job.status) ? false : 1000 } })
  const cancel = useMutation({ mutationFn: () => api.cancelJob(run!.jobId), onSuccess: () => query.refetch() })
  const job = query.data
  const artifactId = job?.result?.artifact_id
  const artifact = useQuery({ queryKey: ['render-artifact', projectId, artifactId], queryFn: () => api.getRenderArtifact(projectId, artifactId!), enabled: job?.status === 'COMPLETED' && Boolean(artifactId), retry: 1 })
  const active = job?.status === 'PENDING' || job?.status === 'RUNNING'
  const historicalArtifact = !job || job.status === 'COMPLETED' ? recoveredArtifact : undefined
  return { query, job, cancel, artifact: artifact.data ?? historicalArtifact, artifactError: artifact.error, active }
}

function RenderJobView({ label, state, stale = false }: { label: string; state: ReturnType<typeof useRenderJob>; stale?: boolean }) {
  const { job } = state
  if (state.query.isError) return <RenderError title={`Não foi possível acompanhar ${label.toLowerCase()}.`} error={state.query.error} />
  const progress = job ? Math.round(Math.max(0, Math.min(1, job.progress)) * 100) : state.artifact ? 100 : 0
  const artifact = state.artifact
  const contentUrl = artifact ? mediaContentUrl(artifact.content_url || artifactContentUrl(artifact.project_id, artifact.id)) : undefined
  return <div className="rounded-lg border border-slate-700 p-3" aria-live="polite">
    <div className="flex justify-between gap-3 text-sm"><span>{job ? statusLabels[job.status] : state.artifact ? 'Concluído' : 'Consultando…'}</span><span>{progress}%</span></div>
    <div className="mt-2 h-2 overflow-hidden rounded bg-slate-800" role="progressbar" aria-label={`Progresso: ${label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><div className="h-full bg-violet-500" style={{ width: `${progress}%` }} /></div>
    {state.active && <button type="button" className="button-secondary mt-3" disabled={state.cancel.isPending} onClick={() => state.cancel.mutate()}>{state.cancel.isPending || job?.cancellation_requested ? 'Cancelando…' : 'Cancelar'}</button>}
    {state.cancel.isError && <div className="mt-3"><RenderError title="Não foi possível cancelar o render. O job continua sendo acompanhado." error={state.cancel.error} /></div>}
    {job?.status === 'FAILED' && <div className="mt-3"><RenderError title="Não foi possível renderizar o vídeo." error={job.error} /></div>}
    {job?.status === 'CANCELLED' && <div className="mt-3"><StatusMessage>Render cancelado. O projeto e as mídias originais foram preservados.</StatusMessage></div>}
    {(job?.status === 'COMPLETED' || artifact) && stale && <div className="mt-3"><StatusMessage>Este arquivo representa uma configuração anterior.</StatusMessage></div>}
    {(job?.status === 'COMPLETED' || artifact) && contentUrl && <div className="mt-3 space-y-2"><video className="max-h-96 w-full rounded-lg bg-black" controls preload="metadata" src={contentUrl}>Seu navegador não suporta vídeo HTML5.</video><a className="button-secondary" href={contentUrl} download>Baixar {label.toLowerCase()}</a><p className="text-xs text-slate-400">Salvo em {artifact?.kind === 'FINAL' ? 'renders' : 'previews'} deste projeto · {artifact?.width}×{artifact?.height} · {formatBytes(artifact?.size_bytes ?? 0)}</p></div>}
    {job?.status === 'COMPLETED' && !contentUrl && <div className="mt-3"><StatusMessage tone="error">O job terminou, mas o backend não informou o arquivo gerado.</StatusMessage></div>}
    {state.artifactError && <div className="mt-3"><RenderError title="O render terminou, mas o arquivo não pôde ser localizado." error={state.artifactError} /></div>}
  </div>
}

function formatBytes(value: number): string { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB` }

function RenderError({ title, error }: { title: string; error: unknown }) {
  const cause = typeof error === 'object' && error && 'message' in error && typeof error.message === 'string' ? error.message : errorMessage(error)
  const details = typeof error === 'object' && error && 'details' in error && error.details && typeof error.details === 'object' ? error.details as Record<string, unknown> : undefined
  const code = typeof error === 'object' && error && 'code' in error ? String(error.code) : undefined
  const technical = typeof details?.technical === 'string' ? details.technical : undefined
  return <StatusMessage tone="error"><span>{title}</span><span className="mt-1 block">Possível causa: {cause}</span>{(code || technical) && <details className="mt-2"><summary className="cursor-pointer">Detalhes técnicos</summary>{code && <code className="mt-1 block break-all text-xs">{code}</code>}{technical && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs">{technical}</pre>}</details>}</StatusMessage>
}
