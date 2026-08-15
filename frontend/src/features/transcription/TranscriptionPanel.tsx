import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { Job, MediaAsset, TranscriptionPreset } from '../../types/api'
import { TranscriptView } from './TranscriptView'

const terminal = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])
const presets: Array<{ value: TranscriptionPreset; label: string; description: string }> = [
  { value: 'ECONOMY', label: 'Econômico', description: 'Menor uso de memória e CPU.' },
  { value: 'BALANCED', label: 'Balanceado', description: 'Bom equilíbrio para uso geral.' },
  { value: 'QUALITY', label: 'Qualidade', description: 'Mais precisão, processamento mais lento.' },
  { value: 'MAXIMUM_QUALITY', label: 'Máxima qualidade', description: 'Maior modelo local disponível.' },
]

export function TranscriptionPanel({ projectId, media, transcriptionAvailable }: { projectId: string; media: MediaAsset[]; transcriptionAvailable?: boolean }) {
  const [mediaId, setMediaId] = useState('')
  const [trackIndex, setTrackIndex] = useState('')
  const [preset, setPreset] = useState<TranscriptionPreset>('BALANCED')
  const [language, setLanguage] = useState('')
  const [wordTimestamps, setWordTimestamps] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const selectedMedia = useMemo(() => media.find((item) => item.id === mediaId), [media, mediaId])
  const start = useMutation({ mutationFn: () => api.startTranscription(projectId, { media_id: mediaId, audio_stream_index: Number(trackIndex), preset, language: language.trim() || null, word_timestamps: wordTimestamps }), onSuccess: (job) => setJobId(job.id) })
  const job = useQuery({ queryKey: ['job', jobId], queryFn: () => api.getJob(jobId!), enabled: Boolean(jobId), refetchInterval: (query) => { const data = query.state.data as Job | undefined; return data && terminal.has(data.status) ? false : 1000 } })
  const transcriptId = job.data?.result?.transcript_id
  const transcript = useQuery({ queryKey: ['transcript', projectId, transcriptId], queryFn: () => api.getTranscript(projectId, transcriptId!), enabled: job.data?.status === 'COMPLETED' && Boolean(transcriptId) })
  const cancel = useMutation({ mutationFn: () => api.cancelJob(jobId!), onSuccess: (data) => job.refetch().then(() => data) })
  const submit = (event: FormEvent) => { event.preventDefault(); if (mediaId && trackIndex !== '') start.mutate() }
  const active = job.data && (job.data.status === 'PENDING' || job.data.status === 'RUNNING')
  const progress = Math.max(0, Math.min(100, (job.data?.progress ?? 0) * 100))

  return <div className="space-y-4"><section className="panel" aria-labelledby="transcription-title"><h3 id="transcription-title" className="font-semibold">Transcrição local</h3><p className="mt-1 text-sm text-slate-400">Selecione explicitamente a mídia e a track de áudio. O processamento usa faster-whisper local.</p>
    {transcriptionAvailable === false && <div className="mt-4"><StatusMessage tone="error">faster-whisper não está disponível. Instale a dependência local e reinicie o backend para habilitar a transcrição.</StatusMessage></div>}
    {media.every((item) => item.probe.audio_streams.length === 0) ? <div className="mt-4"><StatusMessage>Nenhuma mídia com áudio está disponível para transcrição.</StatusMessage></div> : <form className="mt-4 space-y-4" onSubmit={submit}>
      <div className="grid gap-4 sm:grid-cols-2"><div><label className="label" htmlFor="transcription-media">Fonte de mídia</label><select className="field" id="transcription-media" value={mediaId} onChange={(e) => { setMediaId(e.target.value); setTrackIndex('') }} disabled={Boolean(active)}><option value="">Selecione uma mídia</option>{media.filter((item) => item.probe.audio_streams.length > 0).map((item) => <option key={item.id} value={item.id}>{item.role === 'SCREEN' ? 'Tela' : 'Webcam'} — {item.original_filename}</option>)}</select></div>
        <div><label className="label" htmlFor="audio-track">Track de áudio</label><select className="field" id="audio-track" value={trackIndex} onChange={(e) => setTrackIndex(e.target.value)} disabled={!selectedMedia || Boolean(active)}><option value="">Selecione uma track</option>{selectedMedia?.probe.audio_streams.map((track) => <option key={track.index} value={track.index}>Track {track.index} — {track.codec_name}, {track.channels ?? '?'} canais{track.language ? `, ${track.language}` : ''}</option>)}</select></div></div>
      <fieldset disabled={Boolean(active)}><legend className="label">Preset</legend><div className="grid gap-2 sm:grid-cols-2">{presets.map((item) => <label key={item.value} className={`cursor-pointer rounded-lg border p-3 ${preset === item.value ? 'border-sky-500 bg-sky-950/30' : 'border-slate-700'}`}><span className="flex items-center gap-2"><input type="radio" name="preset" value={item.value} checked={preset === item.value} onChange={() => setPreset(item.value)} /><span className="font-medium">{item.label}</span></span><span className="mt-1 block pl-6 text-xs text-slate-400">{item.description}</span></label>)}</div></fieldset>
      <div className="grid gap-4 sm:grid-cols-2"><div><label className="label" htmlFor="language">Idioma (opcional)</label><input className="field" id="language" value={language} onChange={(e) => setLanguage(e.target.value)} placeholder="Automático (ex.: pt)" disabled={Boolean(active)} /></div><label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={wordTimestamps} onChange={(e) => setWordTimestamps(e.target.checked)} disabled={Boolean(active)} /> Preservar timestamps por palavra</label></div>
      <button className="button" disabled={transcriptionAvailable === false || !mediaId || trackIndex === '' || start.isPending || Boolean(active)}>{start.isPending ? 'Iniciando…' : 'Iniciar transcrição'}</button>
    </form>}
    {start.isError && <div className="mt-3"><StatusMessage tone="error">{errorMessage(start.error)}</StatusMessage></div>}
    {jobId && <div className="mt-5 border-t border-slate-800 pt-4" aria-live="polite">{job.isPending ? <p className="text-sm text-slate-400">Consultando job…</p> : job.isError ? <StatusMessage tone="error">Falha ao consultar o job: {errorMessage(job.error)}</StatusMessage> : job.data && <div className="space-y-3"><div className="flex items-center justify-between text-sm"><span>Estado: <strong>{job.data.status}</strong></span><span>{Math.round(progress)}%</span></div><div className="h-2 overflow-hidden rounded bg-slate-800" role="progressbar" aria-label="Progresso da transcrição" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}><div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${progress}%` }} /></div>
          {active && <button type="button" className="button-secondary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? 'Cancelando…' : 'Cancelar'}</button>}
          {job.data.status === 'FAILED' && <StatusMessage tone="error">{job.data.error?.message ?? 'A transcrição falhou.'}</StatusMessage>}{job.data.status === 'CANCELLED' && <StatusMessage>Transcrição cancelada. Você pode ajustar a configuração e iniciar outra.</StatusMessage>}{job.data.status === 'COMPLETED' && !transcriptId && <StatusMessage tone="error">O job terminou sem identificar o transcript.</StatusMessage>}
        </div>}</div>}
  </section>
  {transcript.isPending && transcript.fetchStatus !== 'idle' && <StatusMessage>Carregando transcript…</StatusMessage>}{transcript.isError && <StatusMessage tone="error">{errorMessage(transcript.error)}</StatusMessage>}{transcript.data && <TranscriptView transcript={transcript.data} />}
  </div>
}
