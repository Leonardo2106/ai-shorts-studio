import { useState } from 'react'
import { CapabilitiesBar } from './components/CapabilitiesBar'
import { ProjectList } from './features/projects/ProjectList'
import { ProjectWorkspace } from './features/projects/ProjectWorkspace'
import { useCapabilities } from './hooks/useCapabilities'

export function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const capabilities = useCapabilities()
  const transcriptionAvailable = capabilities.data?.faster_whisper?.available ?? capabilities.data?.faster_whisper?.status === 'AVAILABLE'
  return <div className="min-h-screen bg-[radial-gradient(circle_at_top_right,_#12233d,_#080c14_40%)]"><header className="border-b border-slate-800 bg-slate-950/70 backdrop-blur"><div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-5 sm:px-6"><div><h1 className="text-xl font-bold">AI Shorts Studio</h1><p className="text-sm text-slate-400">Importe, sincronize e transcreva suas gravações localmente.</p></div><CapabilitiesBar query={capabilities} /></div></header>
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[18rem_1fr]"><ProjectList selectedId={selectedId} onSelect={setSelectedId} />{selectedId ? <ProjectWorkspace key={selectedId} projectId={selectedId} transcriptionAvailable={transcriptionAvailable} /> : <main className="panel flex min-h-64 items-center justify-center text-center"><div><h2 className="text-lg font-semibold">Abra um projeto</h2><p className="mt-2 max-w-md text-sm text-slate-400">Selecione um projeto existente ou crie um novo para importar a gravação da tela e da webcam.</p></div></main>}</div>
  </div>
}
