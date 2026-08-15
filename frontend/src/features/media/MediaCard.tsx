import { ChangeEvent, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage, mediaContentUrl } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { MediaAsset, MediaRole } from '../../types/api'

const roleLabel: Record<MediaRole, string> = { SCREEN: 'Tela', WEBCAM: 'Webcam' }
const formatBytes = (size: number) => size < 1_000_000 ? `${(size / 1000).toFixed(0)} KB` : `${(size / 1_000_000).toFixed(1)} MB`
const formatTime = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

export function MediaCard({ projectId, role, media }: { projectId: string; role: MediaRole; media?: MediaAsset }) {
  const [previewError, setPreviewError] = useState(false)
  const client = useQueryClient()
  const upload = useMutation({ mutationFn: (file: File) => api.uploadMedia(projectId, role, file), onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ['project', projectId] }), client.invalidateQueries({ queryKey: ['projects'] })]) } })
  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = '' }

  return <section className="panel" aria-labelledby={`media-${role}`}>
    <div className="flex items-start justify-between gap-3"><div><h3 id={`media-${role}`} className="font-semibold">{roleLabel[role]}</h3><p className="text-xs text-slate-400">{role === 'SCREEN' ? 'Gravação principal da tela' : 'Câmera do apresentador'}</p></div>
      {!media ? <label className="button-secondary cursor-pointer"><span>{upload.isPending ? 'Importando…' : 'Importar'}</span><input className="sr-only" type="file" accept="video/*,.mp4" onChange={chooseFile} disabled={upload.isPending} aria-label={`Importar ${roleLabel[role]}`} /></label> : <span className="rounded-full border border-emerald-800 px-2.5 py-1 text-xs text-emerald-300">Associada</span>}
    </div>
    {upload.isError && <div className="mt-3"><StatusMessage tone="error">{errorMessage(upload.error)}</StatusMessage></div>}
    {!media && !upload.isPending && <div className="mt-4 rounded-lg border border-dashed border-slate-700 p-8 text-center text-sm text-slate-400">Nenhum vídeo associado.</div>}
    {upload.isPending && <div className="mt-4" role="status"><div className="mb-2 text-sm text-slate-300">Enviando e inspecionando mídia…</div><div className="h-2 overflow-hidden rounded bg-slate-800"><div className="h-full w-1/2 animate-pulse rounded bg-sky-500" /></div></div>}
    {media && <div className="mt-4 space-y-4">
      {!previewError ? <video key={media.content_url} className="aspect-video w-full rounded-lg bg-black" controls preload="metadata" src={mediaContentUrl(media.content_url)} onError={() => setPreviewError(true)}>Seu navegador não suporta vídeo HTML5.</video> : <StatusMessage tone="error">O navegador não conseguiu reproduzir este codec. A mídia continua importada e pode ser transcrita.</StatusMessage>}
      <div><p className="truncate text-sm font-medium" title={media.original_filename}>{media.original_filename}</p><dl className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-400">
        <div><dt>Duração</dt><dd className="text-slate-200">{formatTime(media.probe.duration_ms)}</dd></div><div><dt>Tamanho</dt><dd className="text-slate-200">{formatBytes(media.size_bytes)}</dd></div>
        <div><dt>Formato</dt><dd className="text-slate-200">{media.probe.format_name || '—'}</dd></div><div><dt>Vídeo</dt><dd className="text-slate-200">{media.probe.video_streams[0] ? `${media.probe.video_streams[0].width ?? '?'}×${media.probe.video_streams[0].height ?? '?'} · ${media.probe.video_streams[0].codec_name ?? 'codec desconhecido'}` : 'Sem stream'}</dd></div>
      </dl></div>
      <div><h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Áudio</h4>{media.probe.audio_streams.length === 0 ? <p className="mt-1 text-sm text-amber-300">Nenhuma track de áudio detectada.</p> : <ul className="mt-1 space-y-1 text-sm">{media.probe.audio_streams.map((audio) => <li key={audio.index} className="rounded bg-slate-950 px-2 py-1.5">Track {audio.index} · {audio.codec_name ?? 'codec desconhecido'} · {audio.channels ?? '?'} canais{audio.language ? ` · ${audio.language}` : ''}</li>)}</ul>}</div>
    </div>}
  </section>
}
