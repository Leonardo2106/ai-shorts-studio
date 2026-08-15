import type { Transcript } from '../../types/api'

const timestamp = (ms: number) => {
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const millis = ms % 1000
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`
}

export function TranscriptView({ transcript }: { transcript: Transcript }) {
  return <section className="panel" aria-labelledby="transcript-title"><div className="flex flex-wrap items-baseline justify-between gap-2"><h3 id="transcript-title" className="font-semibold">Transcript</h3><p className="text-xs text-slate-400">Idioma: {transcript.language ?? 'detectado automaticamente'}</p></div>
    {transcript.segments.length === 0 ? <p className="mt-4 text-sm text-slate-400">A transcrição terminou sem segmentos de fala.</p> : <ol className="mt-4 max-h-[32rem] space-y-2 overflow-y-auto pr-1">{transcript.segments.map((segment, index) => <li key={`${segment.start_ms}-${index}`} className="grid grid-cols-[7.5rem_1fr] gap-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3"><span className="font-mono text-xs text-sky-300"><time>{timestamp(segment.start_ms)}</time> – <time>{timestamp(segment.end_ms)}</time></span><p className="m-0 text-sm leading-6 text-slate-200">{segment.text}</p></li>)}</ol>}
  </section>
}
