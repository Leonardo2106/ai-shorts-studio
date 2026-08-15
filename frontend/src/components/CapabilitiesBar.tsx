import { errorMessage } from '../api/client'
import { useCapabilities } from '../hooks/useCapabilities'
import type { Capability, Capabilities } from '../types/api'

function capabilityEntries(data: Capabilities): Capability[] {
  return ['ffprobe', 'ffmpeg', 'faster_whisper'].flatMap((name) => {
    const value = data[name]
    return value && typeof value === 'object' && ('status' in value || 'available' in value) ? [{ ...(value as Capability), name }] : []
  })
}

export function CapabilitiesBar({ query }: { query: ReturnType<typeof useCapabilities> }) {
  if (query.isPending) return <p className="text-xs text-slate-400" role="status">Verificando ferramentas locais…</p>
  if (query.isError) return <p className="text-xs text-amber-300">Não foi possível consultar ferramentas: {errorMessage(query.error)}</p>
  const entries = capabilityEntries(query.data)
  if (!entries.length) return null
  return <div className="flex flex-wrap gap-2" aria-label="Ferramentas locais">
    {entries.map((item) => { const available = item.available ?? item.status === 'AVAILABLE'; return <span key={item.name} title={item.detail ?? undefined} className={`rounded-full border px-2.5 py-1 text-xs ${available ? 'border-emerald-800 text-emerald-300' : 'border-amber-800 text-amber-300'}`}>
      {item.name === 'faster_whisper' ? 'faster-whisper' : item.name}: {available ? item.version || 'disponível' : item.status === 'ERROR' ? 'erro' : 'não encontrado'}
    </span>})}
  </div>
}
